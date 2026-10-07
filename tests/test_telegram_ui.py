import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, "dcrypt_mt"))

from telegram_ui import TelegramUI  # noqa: E402

from dcrypt_mt.config import Settings  # noqa: E402
from dcrypt_mt.db import Database, Repo  # noqa: E402
from dcrypt_mt.executors.paper import PaperExecutor  # noqa: E402
from dcrypt_mt.models import TokenInfo  # noqa: E402
from dcrypt_mt.notify import Notifier  # noqa: E402
from dcrypt_mt.positions import PositionManager  # noqa: E402


class FakeQuoter:
    async def roundtrip_ok(self, mint, size_sol, **kw):
        return (True, "ok")


class FakePrices:
    async def get_tokens(self, mints):
        return {}

    async def get_token(self, mint):
        return None

    async def resolve(self, address):
        return None

    async def sol_usd(self):
        return 100.0


async def _make(tmp_path):
    s = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path}/t.db", kill_file=str(tmp_path / "KILL"),
                 require_onchain_checks=False, paper_use_jupiter_quotes=False, _env_file=None)
    db = Database(s.db_url)
    await db.init()
    repo = Repo(db)
    pm = PositionManager(s, repo, FakePrices(), PaperExecutor(s, quoter=FakeQuoter()), Notifier())
    return s, db, repo, pm


def _ui(s, pm, loop):
    ui = TelegramUI("mt", s, pm, loop, chat_id="123", token="dummy")
    sent = []
    ui.send = lambda chat_id, text, kb=None: sent.append(text)
    return ui, sent


def test_command_menu_and_pause_resume(tmp_path):
    async def go():
        s, db, repo, pm = await _make(tmp_path)
        ui, sent = _ui(s, pm, asyncio.get_running_loop())
        ui.handle_event({"type": "command", "chat_id": "123", "cmd": "/start"})
        assert sent and "Dcrypt-mt" in sent[-1]
        ui.handle_event({"type": "command", "chat_id": "123", "cmd": "/pause"})
        assert os.path.exists(s.kill_file)
        ui.handle_event({"type": "command", "chat_id": "123", "cmd": "/resume"})
        assert not os.path.exists(s.kill_file)
        ui.handle_event({"type": "command", "chat_id": "999", "cmd": "/start"})  # wrong chat: ignored
        assert len(sent) == 3
        await db.close()
    asyncio.run(go())


def test_apply_setting(tmp_path):
    async def go():
        s, db, repo, pm = await _make(tmp_path)
        ui, _ = _ui(s, pm, asyncio.get_running_loop())
        assert "updated" in ui.apply_setting("hp", "20")
        assert s.hard_stop_pct == 20
        assert "Invalid" in ui.apply_setting("hp", "abc")
        assert "Out of range" in ui.apply_setting("hp", "999")
        await db.close()
    asyncio.run(go())


def test_force_sell_not_found(tmp_path):
    async def go():
        s, db, repo, pm = await _make(tmp_path)
        ui, _ = _ui(s, pm, asyncio.get_running_loop())
        assert "not found" in ui.force_sell_sync("deadbeef", 100)
        await db.close()
    asyncio.run(go())


def test_settings_render(tmp_path):
    async def go():
        s, db, repo, pm = await _make(tmp_path)
        ui, sent = _ui(s, pm, asyncio.get_running_loop())
        ui.handle_event({"type": "command", "chat_id": "123", "cmd": "/settings"})
        assert "Hard stop" in sent[-1]
        await db.close()
    asyncio.run(go())
