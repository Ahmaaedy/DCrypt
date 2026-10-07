from __future__ import annotations

from ..config import Settings
from .base import Fill, Quote


class PaperExecutor:
    """Simulated fills. Pessimistic by design: base slippage + constant-product price impact + exit penalty + fees."""
    mode = "paper"

    def __init__(self, s: Settings):
        self.s = s

    def _impact_bps(self, sol_amount: float, q: Quote) -> float:
        usd = sol_amount * q.sol_usd
        return 2 * usd / max(q.liquidity_usd, 1.0) * 1e4  # each side of the pool holds ~L/2

    async def pre_trade_check(self, mint: str, size_sol: float) -> tuple[bool, str]:
        return True, "ok"

    async def buy(self, mint: str, size_sol: float, q: Quote) -> Fill:
        bps = self.s.paper_base_slippage_bps + self._impact_bps(size_sol, q)
        price = q.price_native * (1 + bps / 1e4)
        return Fill(True, price_native=price, token_amount=size_sol / price, sol_amount=size_sol,
                    fee_sol=self.s.paper_fee_sol, slippage_bps=bps)

    async def sell(self, mint: str, token_amount: float, q: Quote) -> Fill:
        bps = (self.s.paper_base_slippage_bps + self.s.paper_exit_penalty_bps
               + self._impact_bps(token_amount * q.price_native, q))
        price = q.price_native * (1 - min(bps, 9500) / 1e4)
        return Fill(True, price_native=price, token_amount=token_amount, sol_amount=token_amount * price,
                    fee_sol=self.s.paper_fee_sol, slippage_bps=bps)
