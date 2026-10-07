import asyncio

from sqlalchemy import text

from helpers import make_env, msg
from dcrypt_mt.stats import report


def test_report_runs(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path)
        info = prices.add()
        await pipe.handle_message(msg(f"buy {info.mint}", name="alpha"))
        p = (await repo.open_positions())[0]
        prices.set(info.mint, price_native=p.entry_price * 0.5)
        await pm.tick()
        out = await report(db)
        assert "alpha" in out and "Execution" in out
        await db.close()
    asyncio.run(go())


def test_report_acceleration_split(tmp_path):
    async def go():
        s, db, repo, prices, val, pm, pipe = await make_env(tmp_path)
        info = prices.add()
        await pipe.handle_message(msg(f"buy $TST {info.mint}"))
        info2 = prices.add(buys_h1=2000, sells_h1=1800)
        await pipe.handle_message(msg(f"buy $TST {info2.mint}", mid=2, name="alpha"))
        async with db.engine.begin() as c:
            for sid in (1, 2):
                await c.execute(text(
                    "INSERT INTO price_snapshots (signal_id, mint, offset_s, price_native, liquidity_usd, taken_at)"
                    " VALUES (:sid, 'm', 300, 1.1, 1000, CURRENT_TIMESTAMP)"), {"sid": sid})
        out = await report(db)
        assert "Acceleration gate" in out and "passed" in out and "failed" in out
        await db.close()
    asyncio.run(go())
