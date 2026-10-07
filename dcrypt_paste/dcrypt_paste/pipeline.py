"""Message -> filter -> normalize -> resolve -> validate -> open position. Runs N concurrent workers."""
from __future__ import annotations

import asyncio
import logging

from .db import Repo
from .filter import score_message
from .models import RawMessage
from .normalizer import Normalizer
from .positions import PositionManager
from .prices import PriceSource
from .validator import SignalCtx, Validator
from .config import Settings

log = logging.getLogger("dcrypt.pipeline")


class Pipeline:
    def __init__(self, s: Settings, repo: Repo, normalizer: Normalizer, prices: PriceSource,
                 validator: Validator, positions: PositionManager):
        self.s, self.repo, self.normalizer = s, repo, normalizer
        self.prices, self.validator, self.positions = prices, validator, positions

    async def handle_message(self, raw: RawMessage) -> None:
        res = score_message(raw.text)
        passed = bool(res.mints) and res.score >= self.s.filter_threshold
        msg_pk = await self.repo.add_message(raw, res.score, res.mints, passed)
        if msg_pk is None or not passed:  # duplicate edit, or not signal-shaped (still logged for tuning)
            return

        norm = await self.normalizer.normalize(raw.text, res.mints)
        sig = norm.signal
        sig_id = await self.repo.add_signal(
            msg_pk, raw, mint=norm.mint, symbol=sig.symbol if sig else None, side=sig.side if sig else None,
            entry=sig.entry_price if sig else None, stop=sig.stop_price if sig else None,
            targets=sig.targets if sig else None, status=norm.status, llm_raw=norm.raw, latency_ms=norm.latency_ms)
        if norm.status != "ok" or sig is None or norm.mint is None:
            await self.repo.add_decision(sig_id, False, norm.status)
            return

        info = await self.prices.resolve(norm.mint)
        if info:
            await self.repo.update_signal_mint(sig_id, info.mint, info.symbol)
            await self.repo.add_snapshot(sig_id, info.mint, 0, info)  # offset-0 baseline, even if we reject
        if sig.side != "buy":  # long-only: sell calls are logged, never traded
            await self.repo.add_decision(sig_id, False, "not_buy")
            return

        ctx = SignalCtx(sig_id, raw.channel_id, raw.ts, info.mint if info else norm.mint)
        d = await self.validator.evaluate(ctx, info)
        await self.repo.add_decision(sig_id, d.accepted, d.reason, d.size_sol, d.details)
        log.info("signal %s ch=%s %s -> %s", sig_id, raw.channel_name, info.symbol if info else norm.mint[:6],
                 d.reason)
        if not d.accepted or info is None:
            return
        try:
            await self.positions.open(sig_id, raw.channel_id, info, d.size_sol)
        finally:
            await self.validator.release(info.mint)

    async def worker(self, queue: asyncio.Queue) -> None:
        while True:
            raw = await queue.get()
            try:
                await self.handle_message(raw)
            except Exception:
                log.exception("failed handling message %s/%s", raw.channel_id, raw.message_id)
            finally:
                queue.task_done()
