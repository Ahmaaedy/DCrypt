"""Live executor via Jupiter + raw JSON-RPC. UNTESTED against mainnet in this repo's test-suite:
prove it with a tiny position first (see README)."""
from __future__ import annotations

import asyncio
import base64

import httpx

from ..config import Settings
from ..prices import SOL_MINT
from ..rpc import Rpc
from .base import Fill, Quote

LAMPORTS = 1_000_000_000


class LiveExecutor:
    mode = "live"

    def __init__(self, s: Settings, rpc: Rpc, client: httpx.AsyncClient | None = None, quoter=None):
        from solders.keypair import Keypair  # lazy: only needed in live mode

        from .quoter import JupiterQuoter

        self.s, self.rpc = s, rpc
        self.kp = Keypair.from_base58_string(s.wallet_secret_key)
        self.owner = str(self.kp.pubkey())
        headers = {"x-api-key": s.jupiter_api_key} if s.jupiter_api_key else {}
        self.http = client or httpx.AsyncClient(timeout=20.0, headers=headers)
        self.base = s.jupiter_base_url.rstrip("/")
        self.quoter = quoter or JupiterQuoter(s.jupiter_base_url, s.jupiter_api_key, self.http)

    # ---- jupiter
    async def _quote(self, inp: str, out: str, amount: int, slippage_bps: int) -> dict:
        r = await self.http.get(f"{self.base}/quote", params={
            "inputMint": inp, "outputMint": out, "amount": amount, "slippageBps": slippage_bps,
            "restrictIntermediateTokens": "true"})
        r.raise_for_status()
        return r.json()

    async def _swap(self, quote: dict) -> tuple[str | None, str | None]:
        from solders.transaction import VersionedTransaction

        r = await self.http.post(f"{self.base}/swap", json={
            "quoteResponse": quote, "userPublicKey": self.owner, "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": {"priorityLevelWithMaxLamports": {
                "maxLamports": self.s.max_priority_lamports, "priorityLevel": "high"}}})
        r.raise_for_status()
        tx = VersionedTransaction.from_bytes(base64.b64decode(r.json()["swapTransaction"]))
        signed = VersionedTransaction(tx.message, [self.kp])
        sig = await self.rpc.send_tx(base64.b64encode(bytes(signed)).decode())
        ok, err = await self.rpc.confirm(sig)
        return (sig, None) if ok else (sig, err)

    # ---- checks
    async def pre_trade_check(self, mint: str, size_sol: float) -> tuple[bool, str]:
        """Round-trip quote via the shared JupiterQuoter. Catches no-route tokens and
        extreme impact. It does NOT prove sellability (honeypots can still pass a quote)."""
        ok, why = await self.quoter.roundtrip_ok(
            mint, size_sol, slippage_bps_buy=self.s.slippage_bps_buy,
            slippage_bps_sell=self.s.slippage_bps_sell,
            max_roundtrip_loss_pct=self.s.max_roundtrip_loss_pct,
            max_price_impact_pct=self.s.max_price_impact_pct)
        if not ok and why.startswith("quote_error"):
            why = why.replace("quote_error", "quote_failed", 1)
        return ok, why

    # ---- trading
    async def buy(self, mint: str, size_sol: float, q: Quote) -> Fill:
        try:
            before, _ = await self.rpc.token_balance(self.owner, mint)
            quote = await self._quote(SOL_MINT, mint, int(size_sol * LAMPORTS), self.s.slippage_bps_buy)
            sig, err = await self._swap(quote)
            if err:
                return Fill(False, tx_sig=sig, error=err)
            meta = await self.rpc.tx_meta(sig)
            after, dec = before, None
            for _ in range(6):  # token account balance can lag the confirmation
                after, dec = await self.rpc.token_balance(self.owner, mint)
                if after > before:
                    break
                await asyncio.sleep(1.0)
            if not meta or dec is None or after <= before:
                return Fill(False, tx_sig=sig, error="fill_not_verified")
            tokens = (after - before) / 10 ** dec
            fee = meta["fee"] / LAMPORTS
            spent = (meta["preBalances"][0] - meta["postBalances"][0]) / LAMPORTS - fee  # incl. any ATA rent
            price = spent / tokens
            return Fill(True, price_native=price, token_amount=tokens, sol_amount=spent, fee_sol=fee,
                        slippage_bps=(price / q.price_native - 1) * 1e4 if q.price_native else 0.0, tx_sig=sig)
        except Exception as e:
            return Fill(False, error=f"{type(e).__name__}: {e}")

    async def sell(self, mint: str, token_amount: float, q: Quote) -> Fill:
        try:
            raw_bal, dec = await self.rpc.token_balance(self.owner, mint)
            if dec is None or raw_bal == 0:
                return Fill(False, error="no_token_balance")
            raw = min(int(token_amount * 10 ** dec), raw_bal)
            if raw_bal - raw < max(1, raw_bal // 1000):  # dust: sell everything
                raw = raw_bal
            quote = await self._quote(mint, SOL_MINT, raw, self.s.slippage_bps_sell)
            sig, err = await self._swap(quote)
            if err:
                return Fill(False, tx_sig=sig, error=err)
            meta = await self.rpc.tx_meta(sig)
            if not meta:
                return Fill(False, tx_sig=sig, error="fill_not_verified")
            fee = meta["fee"] / LAMPORTS
            got = (meta["postBalances"][0] - meta["preBalances"][0]) / LAMPORTS + fee
            tokens = raw / 10 ** dec
            price = got / tokens if tokens else 0.0
            return Fill(True, price_native=price, token_amount=tokens, sol_amount=got, fee_sol=fee,
                        slippage_bps=(1 - price / q.price_native) * 1e4 if q.price_native else 0.0, tx_sig=sig)
        except Exception as e:
            return Fill(False, error=f"{type(e).__name__}: {e}")
