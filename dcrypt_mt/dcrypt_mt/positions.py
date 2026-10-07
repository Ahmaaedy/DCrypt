"""Opens positions and runs the exit logic. Exits keep running even when the kill switch is on."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from .config import Settings
from .db import Position, Repo
from .executors.base import Executor, Fill, Quote
from .models import TokenInfo, utcnow
from .notify import Notifier
from .prices import PriceSource

log = logging.getLogger("dcrypt.positions")


def decide_exit(p: Position, info: TokenInfo, now: datetime, s: Settings) -> tuple[float, str] | None:
    """Pure function: returns (fraction_of_remaining_to_sell, reason) or None. Priority order matters."""
    price, entry = info.price_native, p.entry_price
    peak = max(p.peak_price, price)
    held_min = (now - p.opened_at).total_seconds() / 60

    if p.entry_liquidity_usd and info.liquidity_usd < p.entry_liquidity_usd * (1 - s.liq_drop_exit_pct / 100):
        return 1.0, "liquidity_drop"
    if p.scaled_out and price <= entry:
        return 1.0, "breakeven_stop"  # after the scale-out, don't let the runner turn a win into a loss
    if price <= entry * (1 - s.hard_stop_pct / 100):
        return 1.0, "hard_stop"
    if not p.scaled_out and price >= entry * s.tp1_mult:
        return s.scale_out_fraction, "scale_out"
    if peak >= entry * (1 + s.trail_arm_pct / 100) and price <= peak * (1 - s.trail_pct / 100):
        return 1.0, "trailing_stop"
    if held_min >= s.time_stop_min and peak < entry * (1 + s.time_stop_min_gain_pct / 100):
        return 1.0, "time_stop"
    if held_min >= s.max_hold_min:
        return 1.0, "max_hold"
    if (s.fade_exit and info.buys_m5 + info.sells_m5 >= s.min_m5_txns
            and info.buy_ratio_m5 < s.fade_buy_ratio and price < peak):
        return 1.0, "fade"
    return None


class PositionManager:
    def __init__(self, s: Settings, repo: Repo, prices: PriceSource, executor: Executor, notifier: Notifier):
        self.s, self.repo, self.prices, self.executor, self.notify = s, repo, prices, executor, notifier
        self._busy: set[int] = set()

    async def open(self, signal_id: int, channel_id: int, info: TokenInfo, size_sol: float) -> int | None:
        sol_usd = await self.prices.sol_usd()
        fill = await self.executor.buy(info.mint, size_sol, Quote(info.price_native, info.liquidity_usd, sol_usd))
        if not fill.ok:
            await self.repo.add_failed_order(signal_id=signal_id, position_id=None, mode=self.executor.mode,
                                             side="buy", reason="entry", error=fill.error or "unknown")
            await self.notify.send(f"BUY FAILED {info.symbol}: {fill.error}")
            return None
        pid = await self.repo.record_entry(signal_id=signal_id, channel_id=channel_id, info=info,
                                           mode=self.executor.mode, fill=fill)
        await self.notify.send(f"[{self.executor.mode}] OPEN {info.symbol} {fill.sol_amount:.3f} SOL "
                               f"@ {fill.price_native:.3e} (slip {fill.slippage_bps:.0f}bps)")
        return pid

    async def force_sell(self, mint: str, fraction: float = 1.0, reason: str = "manual_telegram") -> str:
        positions = await self.repo.open_positions()
        target = next((p for p in positions if p.mint.startswith(mint)), None)
        if target is None:
            return "position not found (already closed?)"
        sol_usd = await self.prices.sol_usd()
        info = await self.prices.get_token(target.mint)
        await self._exit(target, fraction, reason, info, sol_usd)
        return f"sell {fraction:.0%} requested for {target.symbol or target.mint[:8]}"

    async def tick(self) -> None:
        positions = await self.repo.open_positions()
        if not positions:
            return
        infos = await self.prices.get_tokens(list({p.mint for p in positions}))
        sol_usd = await self.prices.sol_usd()
        now = utcnow()
        await asyncio.gather(*(self._manage(p, infos.get(p.mint), sol_usd, now) for p in positions))

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("position tick failed")
            await asyncio.sleep(self.s.poll_interval_s)

    async def _manage(self, p: Position, info: TokenInfo | None, sol_usd: float, now: datetime) -> None:
        if p.id in self._busy:
            return
        self._busy.add(p.id)
        try:
            if info is None:
                misses = p.price_misses + 1
                await self.repo.update_mark(p.id, None, p.peak_price, misses)
                if misses >= self.s.max_price_misses:
                    await self._exit(p, 1.0, "no_price", None, sol_usd)
                return
            peak = max(p.peak_price, info.price_native)
            await self.repo.update_mark(p.id, info.price_native, peak, 0)
            p.peak_price, p.last_price = peak, info.price_native
            decision = decide_exit(p, info, now, self.s)
            if decision:
                await self._exit(p, decision[0], decision[1], info, sol_usd)
        except Exception:
            log.exception("managing position %s failed", p.id)
        finally:
            self._busy.discard(p.id)

    async def _exit(self, p: Position, fraction: float, reason: str, info: TokenInfo | None, sol_usd: float) -> None:
        full = fraction >= 0.999
        tokens = p.token_amount if full else p.token_amount * fraction

        if reason == "no_price" and self.executor.mode == "paper":
            # pessimistic: a token with no market for ~1 min is treated as a total loss in paper mode
            fill = Fill(True, price_native=0.0, token_amount=tokens, sol_amount=0.0, fee_sol=0.0)
            reason = "no_price_writeoff"
        else:
            q = Quote(info.price_native if info else (p.last_price or p.entry_price),
                      info.liquidity_usd if info else max(p.entry_liquidity_usd * 0.3, 1.0), sol_usd)
            fill = await self.executor.sell(p.mint, tokens, q)

        if not fill.ok:
            n = await self.repo.bump_sell_failures(p.id)
            await self.repo.add_failed_order(signal_id=p.signal_id, position_id=p.id, mode=p.mode, side="sell",
                                             reason=reason, error=fill.error or "unknown")
            if n == self.s.max_sell_failures:
                await self.notify.send(f"STUCK POSITION {p.symbol} ({p.mint}): {n} failed sells. Intervene manually.")
            return

        row = await self.repo.apply_sell(p, fill, reason, closed=full, scaled=reason == "scale_out")
        if full:
            await self.notify.send(f"[{p.mode}] CLOSE {p.symbol} {reason} pnl {row.realized_pnl_sol:+.4f} SOL "
                                   f"({row.pnl_pct:+.1f}%)")
        else:
            await self.notify.send(f"[{p.mode}] SCALE-OUT {p.symbol} sold {fraction:.0%}")
