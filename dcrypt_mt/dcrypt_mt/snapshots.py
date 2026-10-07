"""Records price at +1m/+5m/+15m/+1h for EVERY signal (traded or not) -> per-channel quality stats."""
from __future__ import annotations

import asyncio
import logging

from .config import SNAPSHOT_OFFSETS_S, Settings
from .db import Repo
from .models import utcnow
from .prices import PriceSource

log = logging.getLogger("dcrypt.snapshots")


class SnapshotWorker:
    def __init__(self, s: Settings, repo: Repo, prices: PriceSource):
        self.s, self.repo, self.prices = s, repo, prices

    async def tick(self) -> int:
        now, written = utcnow(), 0
        for off in SNAPSHOT_OFFSETS_S:
            sigs = await self.repo.signals_needing_snapshot(off, now)
            if not sigs:
                continue
            infos = await self.prices.get_tokens(list({sg.mint for sg in sigs}))
            for sg in sigs:
                # a missing pair is recorded as NULL on purpose: dead/rugged tokens must not vanish from stats
                await self.repo.add_snapshot(sg.id, sg.mint, off, infos.get(sg.mint))
                written += 1
        return written

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("snapshot tick failed")
            await asyncio.sleep(10)
