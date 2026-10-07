"""Telethon ingestion: one client, many channels, never blocks the event loop (handlers only enqueue)."""
from __future__ import annotations

import asyncio
import logging
from datetime import timezone

from .config import Settings
from .models import RawMessage, utcnow

log = logging.getLogger("dcrypt.ingest")


def _naive_utc(dt) -> object:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


class TelegramIngest:
    def __init__(self, s: Settings, queue: asyncio.Queue):
        from telethon import TelegramClient

        self.s, self.queue = s, queue
        if s.tg_session_string:
            from telethon.sessions import StringSession
            self.client = TelegramClient(StringSession(s.tg_session_string), s.tg_api_id, s.tg_api_hash)
        else:
            self.client = TelegramClient(s.tg_session, s.tg_api_id, s.tg_api_hash)
        self.names: dict[int, str] = {}

    async def start(self) -> None:
        from telethon import events, utils

        await self.client.start()  # first run prompts for phone + login code
        entities = []
        for ref in self.s.channel_list:
            ent = await self.client.get_entity(ref)
            cid = utils.get_peer_id(ent)
            self.names[cid] = getattr(ent, "title", None) or getattr(ent, "username", None) or str(cid)
            entities.append(ent)
        if not entities:
            raise SystemExit("TG_CHANNELS is empty. Run `python -m dcrypt_paste dialogs` to list channel ids.")
        self.client.add_event_handler(self._on_new, events.NewMessage(chats=entities))
        self.client.add_event_handler(self._on_edit, events.MessageEdited(chats=entities))
        log.info("listening on %d channels: %s", len(entities), ", ".join(self.names.values()))

    async def run(self) -> None:
        await self.start()
        await self.client.run_until_disconnected()

    async def _on_new(self, event) -> None:  # noqa: ANN001
        self._enqueue(event, False)

    async def _on_edit(self, event) -> None:  # noqa: ANN001
        self._enqueue(event, True)  # channels often add the CA/targets by editing

    def _enqueue(self, event, is_edit: bool) -> None:  # noqa: ANN001
        m = event.message
        text = m.message or ""
        if not text:
            return
        when = (m.edit_date if is_edit and m.edit_date else m.date)
        raw = RawMessage(channel_id=event.chat_id, channel_name=self.names.get(event.chat_id, str(event.chat_id)),
                         message_id=m.id, ts=_naive_utc(when), text=text, is_edit=is_edit, received_at=utcnow())
        try:
            self.queue.put_nowait(raw)
        except asyncio.QueueFull:
            log.warning("queue full, dropping message %s/%s", raw.channel_id, raw.message_id)


async def list_dialogs(s: Settings) -> None:
    from telethon import TelegramClient, utils

    async with TelegramClient(s.tg_session, s.tg_api_id, s.tg_api_hash) as client:
        async for d in client.iter_dialogs():
            if d.is_channel or d.is_group:
                print(f"{utils.get_peer_id(d.entity):>16}  {d.name}")
