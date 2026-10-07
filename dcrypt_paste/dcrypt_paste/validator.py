"""Global risk gate. One instance shared by every channel handler, so limits are global, not per-listener."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from .config import Settings
from .db import Repo
from .executors.base import Executor
from .models import TokenInfo, utcnow
from .prices import PriceSource
from .rpc import Rpc


@dataclass
class SignalCtx:
    signal_id: int
    channel_id: int
    msg_ts: object  # datetime
    mint: str


@dataclass
class Decision:
    accepted: bool
    reason: str
    size_sol: float = 0.0
    details: dict = field(default_factory=dict)


class Validator:
    def __init__(self, s: Settings, repo: Repo, prices: PriceSource, executor: Executor, rpc: Rpc | None = None):
        self.s, self.repo, self.prices, self.executor, self.rpc = s, repo, prices, executor, rpc
        self._lock = asyncio.Lock()
        self.inflight: dict[str, float] = {}  # mint -> reserved SOL, held from accept until the entry resolves

    def killed(self) -> bool:
        return Path(self.s.kill_file).exists()

    async def release(self, mint: str) -> None:
        self.inflight.pop(mint, None)

    @staticmethod
    def _no(reason: str, **details) -> Decision:
        return Decision(False, reason, 0.0, details)

    async def evaluate(self, ctx: SignalCtx, info: TokenInfo | None) -> Decision:
        s = self.s
        if self.killed():
            return self._no("kill_switch")

        age = (utcnow() - ctx.msg_ts).total_seconds()
        if age > s.max_signal_age_s:
            return self._no("stale_signal", age_s=round(age, 1))

        if info is None:
            return self._no("no_sol_pair")

        n, avg = await self.repo.channel_perf(ctx.channel_id)
        if n >= s.channel_min_trades and avg < s.channel_min_avg_pnl_pct:
            return self._no("channel_blocked", trades=n, avg_pnl_pct=round(avg, 2))

        calling = await self.repo.channels_calling(info.mint, utcnow() - timedelta(minutes=30))
        details = {"channels_calling": calling, "liquidity_usd": info.liquidity_usd, "price_native": info.price_native,
                   "buy_ratio_m5": round(info.buy_ratio_m5, 3), "change_m5": info.change_m5,
                   "change_h1": info.change_h1, "age_min": info.age_min}
        if calling < s.min_channels:
            return self._no("not_enough_channels", **details)

        if info.liquidity_usd < s.min_liquidity_usd:
            return self._no("low_liquidity", **details)
        if info.age_min is not None and info.age_min < s.min_age_min:
            return self._no("too_new", **details)

        if s.momentum_gates:
            if info.buys_m5 + info.sells_m5 < s.min_m5_txns:
                return self._no("low_activity", **details)
            if info.buy_ratio_m5 < s.min_buy_ratio:
                return self._no("weak_buy_pressure", **details)
            if info.change_m5 is not None:
                if info.change_m5 < s.min_change_m5_pct:
                    return self._no("no_momentum", **details)
                if info.change_m5 > s.max_change_m5_pct:
                    return self._no("already_extended_m5", **details)
            if info.change_h1 is not None and info.change_h1 > s.max_change_h1_pct:
                return self._no("already_extended_h1", **details)

        if self.rpc:
            try:
                ok, why = await self.rpc.check_mint(info.mint, no_freeze=s.require_no_freeze,
                                                    no_mint_auth=s.require_no_mint_authority)
            except Exception as e:
                return self._no("onchain_check_failed", error=str(e)[:120], **details)
            if not ok:
                return self._no(why, **details)

        sol_usd = await self.prices.sol_usd()
        size = min(s.position_size_sol, s.liq_cap_pct / 100 * info.liquidity_usd / max(sol_usd, 1.0))
        if size < s.min_position_sol:
            return self._no("liquidity_too_thin_for_size", size_sol=round(size, 4), **details)

        ok, why = await self.executor.pre_trade_check(info.mint, size)
        if not ok:
            return self._no(f"pre_trade:{why}", **details)

        # --- global state: check + reserve atomically so concurrent channel handlers can't over-allocate
        async with self._lock:
            if info.mint in self.inflight or await self.repo.has_open_position(info.mint):
                return self._no("already_positioned", **details)
            last = await self.repo.last_closed_at(info.mint)
            if last and utcnow() - last < timedelta(minutes=s.cooldown_min):
                return self._no("cooldown", **details)
            reserved = sum(self.inflight.values())
            if await self.repo.count_open() + len(self.inflight) >= s.max_positions:
                return self._no("max_positions", **details)
            if await self.repo.open_exposure_sol() + reserved + size > s.max_exposure_sol:
                return self._no("max_exposure", **details)
            day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            if await self.repo.realized_pnl_since(day_start) <= -s.daily_loss_limit_sol:
                return self._no("daily_loss_limit", **details)
            self.inflight[info.mint] = size
        return Decision(True, "accepted", size, details)
