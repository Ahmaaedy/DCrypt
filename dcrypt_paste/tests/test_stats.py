import asyncio

from helpers import make_env, msg
from dcrypt_paste.stats import report


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
