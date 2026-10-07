"""Read-only Jupiter quoter shared by the paper and live executors.
A round-trip quote proves routing and price impact, NOT sellability:
honeypots can still pass a quote."""
from __future__ import annotations

import httpx

from ..prices import SOL_MINT

LAMPORTS = 1_000_000_000


class JupiterQuoter:
    def __init__(self, base_url: str, api_key: str = "", client: httpx.AsyncClient | None = None):
        headers = {"x-api-key": api_key} if api_key else {}
        self.http = client or httpx.AsyncClient(timeout=20.0, headers=headers)
        self.base = base_url.rstrip("/")

    async def quote(self, inp: str, out: str, amount: int, slippage_bps: int) -> dict:
        r = await self.http.get(f"{self.base}/quote", params={
            "inputMint": inp, "outputMint": out, "amount": amount, "slippageBps": slippage_bps,
            "restrictIntermediateTokens": "true"})
        r.raise_for_status()
        return r.json()

    async def roundtrip_ok(self, mint: str, size_sol: float, *, slippage_bps_buy: int,
                           slippage_bps_sell: int, max_roundtrip_loss_pct: float,
                           max_price_impact_pct: float) -> tuple[bool, str]:
        try:
            lamports = int(size_sol * LAMPORTS)
            q1 = await self.quote(SOL_MINT, mint, lamports, slippage_bps_buy)
            out1 = q1.get("outAmount")
            if not out1:
                return False, "no_route"
            if float(q1.get("priceImpactPct") or 0) * 100 > max_price_impact_pct:
                return False, "price_impact_too_high"
            q2 = await self.quote(mint, SOL_MINT, int(out1), slippage_bps_sell)
            out2 = q2.get("outAmount")
            if not out2:
                return False, "no_route"
            back = int(out2) / lamports
            if back < 1 - max_roundtrip_loss_pct / 100:
                return False, f"roundtrip_loss_{(1 - back) * 100:.0f}pct"
            return True, "ok"
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code in (400, 404):
                return False, "no_route"
            return False, f"quote_error:{type(e).__name__}"
        except httpx.HTTPError as e:
            return False, f"quote_error:{type(e).__name__}"
        except Exception as e:
            return False, f"quote_error:{type(e).__name__}"
