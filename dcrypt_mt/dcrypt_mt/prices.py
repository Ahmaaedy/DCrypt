"""DexScreener price/liquidity/flow source (batched, cached)."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Protocol

import httpx

from .models import TokenInfo

SOL_MINT = "So11111111111111111111111111111111111111112"
BASE = "https://api.dexscreener.com"


class PriceSource(Protocol):
    async def get_tokens(self, mints: list[str]) -> dict[str, TokenInfo]: ...
    async def get_token(self, mint: str) -> TokenInfo | None: ...
    async def resolve(self, address: str) -> TokenInfo | None: ...
    async def sol_usd(self) -> float: ...


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def parse_pairs(pairs: list[dict], wanted: set[str] | None = None) -> dict[str, TokenInfo]:
    """Best (highest-liquidity) SOL-quoted Solana pair per base token."""
    best: dict[str, TokenInfo] = {}
    for p in pairs or []:
        if p.get("chainId") != "solana":
            continue
        base, quote = p.get("baseToken") or {}, p.get("quoteToken") or {}
        mint = base.get("address")
        if not mint or (wanted is not None and mint not in wanted):
            continue
        if quote.get("address") != SOL_MINT and mint != SOL_MINT:
            continue  # we price everything in SOL
        txns = p.get("txns") or {}
        txns_m5 = txns.get("m5") or {}
        txns_h1 = txns.get("h1") or {}
        vol = p.get("volume") or {}
        chg = p.get("priceChange") or {}
        created = p.get("pairCreatedAt")
        info = TokenInfo(
            mint=mint, symbol=base.get("symbol") or "?", pair_address=p.get("pairAddress") or "",
            dex=p.get("dexId") or "", price_native=_f(p.get("priceNative")), price_usd=_f(p.get("priceUsd")),
            liquidity_usd=_f((p.get("liquidity") or {}).get("usd")),
            volume_h24=_f(vol.get("h24")),
            pair_created_at=(datetime.fromtimestamp(created / 1000, timezone.utc).replace(tzinfo=None)
                             if created else None),
            buys_m5=int(txns_m5.get("buys", 0) or 0), sells_m5=int(txns_m5.get("sells", 0) or 0),
            change_m5=chg.get("m5"), change_h1=chg.get("h1"),
            buys_h1=int(txns_h1.get("buys", 0) or 0), sells_h1=int(txns_h1.get("sells", 0) or 0),
            volume_m5=_f(vol.get("m5")), volume_h1=_f(vol.get("h1")),
        )
        if info.price_native <= 0 and mint != SOL_MINT:
            continue
        if mint not in best or info.liquidity_usd > best[mint].liquidity_usd:
            best[mint] = info
    return best


class DexScreener:
    def __init__(self, ttl_s: float = 3.0, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=10.0, base_url=BASE)
        self.ttl = ttl_s
        self._cache: dict[str, tuple[float, TokenInfo | None]] = {}
        self._sol: tuple[float, float] = (0.0, 150.0)
        self._sem = asyncio.Semaphore(4)

    async def _get(self, path: str):
        async with self._sem:
            for attempt in range(3):
                r = await self.client.get(path)
                if r.status_code == 429:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
        return None

    async def get_tokens(self, mints: list[str]) -> dict[str, TokenInfo]:
        now = time.monotonic()
        out: dict[str, TokenInfo] = {}
        need: list[str] = []
        for m in dict.fromkeys(mints):
            hit = self._cache.get(m)
            if hit and now - hit[0] < self.ttl:
                if hit[1]:
                    out[m] = hit[1]
            else:
                need.append(m)
        for i in range(0, len(need), 30):
            chunk = need[i:i + 30]
            try:
                data = await self._get(f"/tokens/v1/solana/{','.join(chunk)}")
            except httpx.HTTPError:
                continue  # leave uncached: caller sees a "miss" and counts it
            found = parse_pairs(data if isinstance(data, list) else [], set(chunk))
            for m in chunk:
                self._cache[m] = (time.monotonic(), found.get(m))
                if m in found:
                    out[m] = found[m]
        return out

    async def get_token(self, mint: str) -> TokenInfo | None:
        return (await self.get_tokens([mint])).get(mint)

    async def resolve(self, address: str) -> TokenInfo | None:
        """Accepts a token mint OR a pair address (links often carry the pair)."""
        info = await self.get_token(address)
        if info:
            return info
        try:
            data = await self._get(f"/latest/dex/pairs/solana/{address}")
        except httpx.HTTPError:
            return None
        pairs = (data or {}).get("pairs") or []
        if not pairs:
            return None
        base = (pairs[0].get("baseToken") or {}).get("address")
        if base == SOL_MINT:  # pair was SOL/X ordered the other way
            base = (pairs[0].get("quoteToken") or {}).get("address")
        return await self.get_token(base) if base else None

    async def sol_usd(self) -> float:
        ts, val = self._sol
        if time.monotonic() - ts < 30:
            return val
        try:
            data = await self._get(f"/tokens/v1/solana/{SOL_MINT}")
            pairs = [p for p in data if p.get("baseToken", {}).get("address") == SOL_MINT]
            if pairs:
                top = max(pairs, key=lambda p: _f((p.get("liquidity") or {}).get("usd")))
                val = _f(top.get("priceUsd"), val)
        except Exception:
            pass
        self._sol = (time.monotonic(), val)
        return val
