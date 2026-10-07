"""Minimal JSON-RPC client (httpx) for the few Solana calls we need."""
from __future__ import annotations

import asyncio
import time

import httpx

BAD_EXTENSIONS = {"transferHook", "permanentDelegate", "pausableConfig"}


class RpcError(Exception):
    pass


class Rpc:
    def __init__(self, url: str, client: httpx.AsyncClient | None = None):
        self.url = url
        self.client = client or httpx.AsyncClient(timeout=15.0)

    async def call(self, method: str, params: list):
        r = await self.client.post(self.url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        r.raise_for_status()
        j = r.json()
        if "error" in j:
            raise RpcError(str(j["error"]))
        return j["result"]

    async def check_mint(self, mint: str, *, no_freeze: bool = True, no_mint_auth: bool = True) -> tuple[bool, str]:
        res = await self.call("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        val = res.get("value")
        if not val:
            return False, "mint_account_not_found"
        parsed = (val.get("data") or {}).get("parsed") or {}
        info = parsed.get("info") or {}
        if parsed.get("type") != "mint":
            return False, "not_a_mint"
        if no_freeze and info.get("freezeAuthority"):
            return False, "freeze_authority_set"
        if no_mint_auth and info.get("mintAuthority"):
            return False, "mint_authority_set"
        exts = {e.get("extension") for e in info.get("extensions", []) or []}
        if exts & BAD_EXTENSIONS:
            return False, f"token2022_extension:{sorted(exts & BAD_EXTENSIONS)[0]}"
        return True, "ok"

    async def token_balance(self, owner: str, mint: str) -> tuple[int, int | None]:
        """(raw amount summed over owner's accounts for this mint, decimals or None if no account)."""
        res = await self.call("getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed"}])
        raw, dec = 0, None
        for acc in res.get("value", []):
            ta = acc["account"]["data"]["parsed"]["info"]["tokenAmount"]
            raw += int(ta["amount"])
            dec = int(ta["decimals"])
        return raw, dec

    async def send_tx(self, tx_b64: str) -> str:
        return await self.call("sendTransaction", [tx_b64, {"encoding": "base64", "skipPreflight": True,
                                                            "maxRetries": 3}])

    async def confirm(self, sig: str, timeout_s: float = 45.0) -> tuple[bool, str | None]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            res = await self.call("getSignatureStatuses", [[sig], {"searchTransactionHistory": False}])
            st = (res.get("value") or [None])[0]
            if st:
                if st.get("err"):
                    return False, str(st["err"])
                if st.get("confirmationStatus") in ("confirmed", "finalized"):
                    return True, None
            await asyncio.sleep(1.0)
        return False, "confirmation_timeout"

    async def tx_meta(self, sig: str) -> dict | None:
        for _ in range(6):
            res = await self.call("getTransaction", [sig, {"encoding": "json", "commitment": "confirmed",
                                                           "maxSupportedTransactionVersion": 0}])
            if res and res.get("meta"):
                return res["meta"]
            await asyncio.sleep(1.0)
        return None
