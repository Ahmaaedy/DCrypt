import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text

from helpers import FakeQuoter, make_env, msg, new_mint
from dcrypt_mt.app import build
from dcrypt_mt.config import Settings
from dcrypt_mt.db import Database, Repo
from dcrypt_mt.models import utcnow


def run(coro):
    return asyncio.run(coro)


async def _decisions(db):
    import json

    async with db.engine.connect() as c:
        rows = (await c.execute(text("SELECT accepted, reason, details FROM decisions ORDER BY id"))).all()
    out = []
    for r in rows:
        d = r[2]
        out.append((r[0], r[1], json.loads(d) if isinstance(d, str) else d))
    return out


# --- 1. fail closed on missing RPC
def test_onchain_fail_closed_rejects_all(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path, require_onchain_checks=True)
        info = prices.add()
        await pipe.handle_message(msg(f"buy $TST {info.mint}"))
        rows = await _decisions(db)
        assert rows and rows[0][1] == "onchain_checks_unavailable"
        assert rows[0][2]["onchain_checked"] is False
        await db.close()
    run(go())


def test_build_raises_without_rpc_when_fail_closed(tmp_path):
    async def go():
        s = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path}/t.db", rpc_url="",
                     require_onchain_checks=True, _env_file=None)
        db = Database(s.db_url)
        await db.init()
        with pytest.raises(SystemExit):
            build(s, db)
        await db.close()
    run(go())


def test_onchain_checked_false_recorded_when_skipped(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path, require_onchain_checks=False)
        info = prices.add()
        await pipe.handle_message(msg(f"buy $TST {info.mint}"))
        rows = await _decisions(db)
        assert rows[0][1] == "accepted"
        assert rows[0][2]["onchain_checked"] is False
        await db.close()
    run(go())


# --- 2. paper pre-trade check uses the quoter
def test_paper_quoter_route_found(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path, _quoter=FakeQuoter((True, "ok")))
        info = prices.add()
        await pipe.handle_message(msg(f"buy $TST {info.mint}"))
        rows = await _decisions(db)
        assert rows[0][1] == "accepted"
        assert rows[0][2]["roundtrip_checked"] is True
        await db.close()
    run(go())


def test_paper_quoter_no_route(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path, _quoter=FakeQuoter((False, "no_route")))
        info = prices.add()
        await pipe.handle_message(msg(f"buy $TST {info.mint}"))
        rows = await _decisions(db)
        assert rows[0][1] == "pre_trade:no_route"
        await db.close()
    run(go())


def test_paper_quoter_error(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(
            tmp_path, _quoter=FakeQuoter((False, "quote_error:ConnectError")))
        info = prices.add()
        await pipe.handle_message(msg(f"buy $TST {info.mint}"))
        rows = await _decisions(db)
        assert rows[0][1] == "pre_trade:quote_error:ConnectError"
        await db.close()
    run(go())


def test_paper_quoter_roundtrip_loss(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(
            tmp_path, _quoter=FakeQuoter((False, "roundtrip_loss_40pct")))
        info = prices.add()
        await pipe.handle_message(msg(f"buy $TST {info.mint}"))
        rows = await _decisions(db)
        assert rows[0][1] == "pre_trade:roundtrip_loss_40pct"
        await db.close()
    run(go())


def test_paper_quoter_disabled(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(
            tmp_path, paper_use_jupiter_quotes=False, _quoter=FakeQuoter((False, "no_route")))
        info = prices.add()
        await pipe.handle_message(msg(f"buy $TST {info.mint}"))
        rows = await _decisions(db)
        assert rows[0][1] == "accepted"
        assert rows[0][2]["roundtrip_checked"] is False
        await db.close()
    run(go())


# --- 3. acceleration gate
def test_acceleration_fail(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path)
        info = prices.add(buys_h1=2000, sells_h1=1800)  # ~0.18x acceleration
        await pipe.handle_message(msg(f"buy $TST {info.mint}"))
        rows = await _decisions(db)
        assert rows[0][1] == "no_acceleration"
        assert rows[0][2]["txn_accel"] is not None
        await db.close()
    run(go())


def test_acceleration_zero_baseline(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path)
        info = prices.add(buys_h1=40, sells_h1=20, volume_h1=125_000.0)  # h1 == m5 -> baseline 0
        await pipe.handle_message(msg(f"buy $TST {info.mint}"))
        rows = await _decisions(db)
        assert rows[0][1] == "baseline_unavailable"
        await db.close()
    run(go())


def test_young_token_reject_vs_skip(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path)
        info = prices.add(pair_created_at=utcnow() - timedelta(minutes=10))
        await pipe.handle_message(msg(f"buy $TST {info.mint}"))
        rows = await _decisions(db)
        assert rows[0][1] == "baseline_unavailable"  # default policy reject
        await db.close()

        sub = tmp_path / "b"
        sub.mkdir()
        s2, db2, repo2, prices2, val2, pm2, pipe2 = await make_env(
            sub, young_token_policy="skip")
        info2 = prices2.add(pair_created_at=utcnow() - timedelta(minutes=10))
        await pipe2.handle_message(msg(f"buy $TST {info2.mint}"))
        rows2 = await _decisions(db2)
        assert rows2[0][1] == "accepted"
        await db2.close()
    run(go())


# --- 4. unrealized losses in daily limit
def test_unrealized_loss_trips_daily_limit(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(
            tmp_path, daily_loss_limit_sol=0.01, hard_stop_pct=90.0)
        info = prices.add()
        await pipe.handle_message(msg(f"buy $TST {info.mint}"))
        assert len(await repo.open_positions()) == 1
        p = (await repo.open_positions())[0]
        prices.set(info.mint, price_native=p.entry_price * 0.5)
        await pm.tick()  # updates last_price to half entry -> unrealized ~ -0.05
        info2 = prices.add()
        await pipe.handle_message(msg(f"buy $TST {info2.mint}", mid=2, name="chan2"))
        rows = await _decisions(db)
        assert rows[-1][1] == "daily_loss_limit"
        assert rows[-1][2]["unrealized_sol"] < 0
        await db.close()
    run(go())


# --- 5. snapshot columns + migration
def test_snapshot_columns_and_null_for_vanished(tmp_path):
    async def go():
        from dcrypt_mt.snapshots import SnapshotWorker
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path)
        info = prices.add()
        await pipe.handle_message(msg(f"buy $TST {info.mint}"))
        async with db.engine.begin() as c:
            await c.execute(text("UPDATE signals SET created_at = :t"),
                            {"t": utcnow() - timedelta(seconds=120)})
        await SnapshotWorker(s, repo, prices).tick()
        async with db.engine.connect() as c:
            row = (await c.execute(text(
                "SELECT txns_m5, volume_m5, txns_h1, volume_h1 FROM price_snapshots WHERE offset_s=60"))).one()
        assert tuple(row) == (60, 125_000.0, 240, 500_000.0)

        prices.tokens.pop(info.mint)  # token vanished
        async with db.engine.begin() as c:
            await c.execute(text("UPDATE signals SET created_at = :t"),
                            {"t": utcnow() - timedelta(seconds=400)})
        await SnapshotWorker(s, repo, prices).tick()
        async with db.engine.connect() as c:
            row = (await c.execute(text(
                "SELECT price_native, txns_m5, volume_m5, txns_h1, volume_h1 FROM price_snapshots WHERE offset_s=300"))).one()
        assert tuple(row) == (None, None, None, None, None)
        await db.close()
    run(go())


def test_migration_adds_snapshot_columns(tmp_path):
    import sqlite3

    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.execute("""CREATE TABLE price_snapshots (id INTEGER PRIMARY KEY, signal_id INTEGER, mint VARCHAR(64),
                     offset_s INTEGER, price_native FLOAT, liquidity_usd FLOAT, taken_at DATETIME)""")
    con.execute("INSERT INTO price_snapshots (signal_id, mint, offset_s, price_native, liquidity_usd, taken_at)"
                " VALUES (1, 'm', 60, 1.0, 100.0, '2026-01-01')")
    con.commit()
    con.close()

    async def go():
        db = Database(f"sqlite+aiosqlite:///{p}")
        await db.init()
        async with db.engine.connect() as c:
            cols = {r[1] for r in (await c.execute(text("PRAGMA table_info(price_snapshots)"))).all()}
            assert {"txns_m5", "volume_m5", "txns_h1", "volume_h1"} <= cols
            n = (await c.execute(text("SELECT COUNT(*) FROM price_snapshots"))).scalar()
            assert n == 1
        await db.close()
    run(go())
