from __future__ import annotations

import logging

import httpx

log = logging.getLogger("dcrypt.notify")


class Notifier:
    def __init__(self, token: str = "", chat_id: str = ""):
        self.token, self.chat_id = token, chat_id
        self.client = httpx.AsyncClient(timeout=10.0) if token and chat_id else None

    async def send(self, text: str) -> None:
        log.info("notify: %s", text)
        if not self.client:
            return
        try:
            await self.client.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                                   json={"chat_id": self.chat_id, "text": text})
        except Exception as e:  # never let notification failures break trading logic
            log.warning("notify failed: %s", e)
