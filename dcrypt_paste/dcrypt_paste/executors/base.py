from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Quote:
    """Market context at execution time (used by the paper fill model)."""
    price_native: float
    liquidity_usd: float
    sol_usd: float


@dataclass
class Fill:
    ok: bool
    price_native: float = 0.0   # SOL per token actually achieved
    token_amount: float = 0.0   # UI units
    sol_amount: float = 0.0     # gross SOL: spent (buy) or received (sell); excludes fee_sol
    fee_sol: float = 0.0
    slippage_bps: float = 0.0
    tx_sig: str | None = None
    error: str | None = None


class Executor(Protocol):
    mode: str

    async def pre_trade_check(self, mint: str, size_sol: float) -> tuple[bool, str]: ...
    async def buy(self, mint: str, size_sol: float, q: Quote) -> Fill: ...
    async def sell(self, mint: str, token_amount: float, q: Quote) -> Fill: ...
