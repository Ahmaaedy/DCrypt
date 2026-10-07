from __future__ import annotations

import asyncio
import logging

from .config import Settings
from .db import Database, Repo
from .ingest import TelegramIngest
from .normalizer import make_normalizer
from .notify import Notifier
from .pipeline import Pipeline
from .positions import PositionManager
from .prices import DexScreener
from .rpc import Rpc
from .snapshots import SnapshotWorker
from .validator import Validator

log = logging.getLogger("dcrypt")


def build(s: Settings, db: Database):
    repo = Repo(db)
    prices = DexScreener()
    rpc = Rpc(s.rpc_url) if s.rpc_url else None
    if s.require_onchain_checks and rpc is None:
        raise SystemExit("REQUIRE_ONCHAIN_CHECKS=true but RPC_URL is empty. "
                         "Set RPC_URL, or set REQUIRE_ONCHAIN_CHECKS=false to run without on-chain safety checks.")
    if not s.require_onchain_checks and rpc is None:
        log.warning("ON-CHAIN SAFETY CHECKS DISABLED: REQUIRE_ONCHAIN_CHECKS=false and no RPC_URL. "
                    "Tokens that live mode would reject will be traded.")
    if s.mode == "live":
        s.assert_live_ready()
        from .executors.live import LiveExecutor
        executor = LiveExecutor(s, rpc)
    else:
        from .executors.paper import PaperExecutor
        executor = PaperExecutor(s)
    notifier = Notifier(s.notify_bot_token, s.notify_chat_id)
    validator = Validator(s, repo, prices, executor, rpc)
    positions = PositionManager(s, repo, prices, executor, notifier)
    pipeline = Pipeline(s, repo, make_normalizer(s), prices, validator, positions)
    return repo, prices, positions, pipeline


async def run(s: Settings, stop_event=None) -> None:
    db = Database(s.db_url)
    await db.init()
    repo, prices, positions, pipeline = build(s, db)
    queue: asyncio.Queue = asyncio.Queue(maxsize=s.queue_size)
    ingest = TelegramIngest(s, queue)
    log.info("starting in %s mode", s.mode.upper())
    tasks = [asyncio.create_task(ingest.run()),
             asyncio.create_task(positions.run()),
             asyncio.create_task(SnapshotWorker(s, repo, prices).run()),
             *[asyncio.create_task(pipeline.worker(queue)) for _ in range(s.workers)]]
    try:
        if stop_event is None:
            await asyncio.gather(*tasks)
        else:
            await asyncio.to_thread(stop_event.wait)
            log.info("stop requested")
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await db.close()
