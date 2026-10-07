from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    """Naive UTC datetime (SQLite drops tzinfo, so we use naive UTC everywhere)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class RawMessage:
    channel_id: int
    channel_name: str
    message_id: int
    ts: datetime  # when the message (or its edit) was posted, naive UTC
    text: str
    is_edit: bool = False
    received_at: datetime = field(default_factory=utcnow)


class LLMSignal(BaseModel):
    """Schema the local LLM must fill. It never sees or produces contract addresses."""

    is_signal: bool
    side: Literal["buy", "sell", "none"] = "none"
    symbol: str | None = None
    mint_ref: int | None = None  # n of the <MINT_n> placeholder being called
    entry_price: float | None = None
    stop_price: float | None = None
    targets: list[float] = Field(default_factory=list)


class TokenInfo(BaseModel):
    mint: str
    symbol: str
    pair_address: str
    dex: str
    price_native: float  # price in SOL
    price_usd: float
    liquidity_usd: float
    volume_h24: float = 0.0
    pair_created_at: datetime | None = None
    buys_m5: int = 0
    sells_m5: int = 0
    change_m5: float | None = None
    change_h1: float | None = None
    buys_h1: int = 0
    sells_h1: int = 0
    volume_m5: float = 0.0
    volume_h1: float = 0.0

    @property
    def age_min(self) -> float | None:
        if not self.pair_created_at:
            return None
        return (utcnow() - self.pair_created_at).total_seconds() / 60

    @property
    def buy_ratio_m5(self) -> float:
        n = self.buys_m5 + self.sells_m5
        return self.buys_m5 / n if n else 0.0
