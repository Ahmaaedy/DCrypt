import asyncio
from datetime import timedelta

from helpers import make_env, msg, new_mint
from dcrypt_mt.models import utcnow
from dcrypt_mt.snapshots import SnapshotWorker


def run(coro):
    return asyncio.run(coro)


def test_accepts_and_opens_paper_position(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path)
        info = prices.add()
        await pipe.handle_message(msg(f"buy $TST {info.mint}"))
        pos = await repo.open_positions()
        assert len(pos) == 1
        p = pos[0]
        assert p.mint == info.mint and p.entry_price > info.price_native  # paid slippage
        assert 0 < p.size_sol <= s.position_size_sol
        await db.close()
    run(go())


def test_rejects_with_reasons(tmp_path):
    async def go():
        s, db, repo, prices, *_ , pipe = await make_env(tmp_path, require_acceleration=False)
        cases = {
            "low_liquidity": prices.add(liquidity_usd=1000),
            "too_new": prices.add(pair_created_at=utcnow() - timedelta(minutes=1)),
            "weak_buy_pressure": prices.add(buys_m5=10, sells_m5=30),
            "already_extended_m5": prices.add(change_m5=120.0),
            "no_momentum": prices.add(change_m5=-5.0),
            "low_activity": prices.add(buys_m5=2, sells_m5=1),
        }
        for i, (reason, info) in enumerate(cases.items()):
            await pipe.handle_message(msg(f"buy {info.mint}", mid=i + 1))
        assert await repo.count_open() == 0
        from sqlalchemy import text
        async with db.engine.connect() as c:
            got = {r[0] for r in (await c.execute(text("select reason from decisions"))).all()}
        assert got == set(cases)
        await db.close()
    run(go())


def test_stale_signal_and_sell_and_no_pair(tmp_path):
    async def go():
        s, db, repo, prices, *_, pipe = await make_env(tmp_path)
        info = prices.add()
        await pipe.handle_message(msg(f"buy {info.mint}", age_s=500, mid=1))        # stale
        await pipe.handle_message(msg(f"sell {info.mint}", mid=2))                   # sell call
        await pipe.handle_message(msg(f"buy {new_mint()}", mid=3))                   # unknown token
        assert await repo.count_open() == 0
        await db.close()
    run(go())


def test_same_token_from_three_channels_buys_once(tmp_path):
    async def go():
        s, db, repo, prices, *_, pipe = await make_env(tmp_path)
        info = prices.add()
        await asyncio.gather(*[pipe.handle_message(msg(f"buy $TST {info.mint}", channel=c, mid=1)) for c in (1, 2, 3)])
        assert await repo.count_open() == 1
        assert await repo.channels_calling(info.mint, utcnow() - timedelta(minutes=5)) == 3
        await db.close()
    run(go())


def test_global_limits_and_kill_switch(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path, max_positions=2)
        mints = [prices.add().mint for _ in range(3)]
        for i, m in enumerate(mints):
            await pipe.handle_message(msg(f"buy {m}", channel=i + 1, mid=1))
        assert await repo.count_open() == 2            # third blocked by GLOBAL cap across channels
        (tmp_path / "KILL").write_text("x")
        extra = prices.add().mint
        await pipe.handle_message(msg(f"buy {extra}", mid=9))
        assert await repo.count_open() == 2
        await db.close()
    run(go())


def test_exit_flow_scaleout_then_breakeven_and_pnl(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path)
        info = prices.add()
        await pipe.handle_message(msg(f"buy {info.mint}"))
        p = (await repo.open_positions())[0]
        prices.set(info.mint, price_native=p.entry_price * 2.1)
        await pm.tick()
        p = (await repo.open_positions())[0]
        assert p.scaled_out and p.token_amount < p.initial_tokens
        prices.set(info.mint, price_native=p.entry_price * 0.99)   # falls back under entry -> breakeven stop
        await pm.tick()
        assert await repo.count_open() == 0
        from sqlalchemy import text
        async with db.engine.connect() as c:
            row = (await c.execute(text("select exit_reason, realized_pnl_sol, pnl_pct from positions"))).one()
        assert row[0] == "breakeven_stop" and row[1] > 0   # locked in profit from the 2x scale-out
        await db.close()
    run(go())


def test_hard_stop_and_liquidity_drop(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path)
        a, b = prices.add(), prices.add()
        await pipe.handle_message(msg(f"buy {a.mint}", mid=1))
        await pipe.handle_message(msg(f"buy {b.mint}", mid=2))
        pa = next(p for p in await repo.open_positions() if p.mint == a.mint)
        prices.set(a.mint, price_native=pa.entry_price * 0.8)
        prices.set(b.mint, liquidity_usd=30_000)
        await pm.tick()
        assert await repo.count_open() == 0
        from sqlalchemy import text
        async with db.engine.connect() as c:
            got = {r[0]: r[1] for r in (await c.execute(text("select mint, exit_reason from positions"))).all()}
        assert got[a.mint] == "hard_stop" and got[b.mint] == "liquidity_drop"
        await db.close()
    run(go())


def test_vanished_token_is_written_off_in_paper(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path, max_price_misses=2)
        info = prices.add()
        await pipe.handle_message(msg(f"buy {info.mint}"))
        del prices.tokens[info.mint]
        await pm.tick(); await pm.tick()
        assert await repo.count_open() == 0
        from sqlalchemy import text
        async with db.engine.connect() as c:
            row = (await c.execute(text("select exit_reason, pnl_pct from positions"))).one()
        assert row[0] == "no_price_writeoff" and row[1] < -99
        await db.close()
    run(go())


def test_snapshots_recorded_for_rejected_signals_incl_vanished(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path)
        info = prices.add(liquidity_usd=1000)           # rejected, but must still be tracked
        await pipe.handle_message(msg(f"buy {info.mint}"))
        from sqlalchemy import text, update
        from dcrypt_mt.db import Signal
        async with repo.sf() as ses:                     # pretend the signal is 5 minutes old
            await ses.execute(update(Signal).values(created_at=utcnow() - timedelta(seconds=305)))
            await ses.commit()
        del prices.tokens[info.mint]                     # token died
        n = await SnapshotWorker(s, repo, prices).tick()
        assert n >= 1
        async with db.engine.connect() as c:
            rows = (await c.execute(text("select offset_s, price_native from price_snapshots order by offset_s"))).all()
        assert rows[0][0] == 0 and rows[0][1] is not None
        assert any(r[0] == 300 and r[1] is None for r in rows)
        await db.close()
    run(go())


def test_duplicate_edit_ignored_and_edit_with_new_text_processed(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path)
        info = prices.add()
        await pipe.handle_message(msg("gm frens", mid=5))                     # no mint -> logged, no signal
        await pipe.handle_message(msg(f"ape {info.mint}", mid=5))             # edit adds CA
        await pipe.handle_message(msg(f"ape {info.mint}", mid=5))             # identical -> dedup
        assert await repo.count_open() == 1
        await db.close()
    run(go())
