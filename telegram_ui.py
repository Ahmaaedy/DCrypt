"""Telegram control UI for Dcrypt variants: command polling, inline keyboards,
pause/resume via KILL file, manual sells via PositionManager.force_sell.

One TelegramUI per variant, each in its own thread+event loop. Only NOTIFY_CHAT_ID
is allowed to control it."""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone

import httpx

log = logging.getLogger("dcrypt.telegram")

API = "https://api.telegram.org/bot{token}/{method}"

# settable runtime knobs: key -> (attr, type, presets, min, max, label)
SETTINGS = {
    "hp": ("hard_stop_pct", float, [10, 15, 20, 25], 5, 50, "Hard Stop %"),
    "tp": ("tp1_mult", float, [1.5, 2.0, 3.0, 4.0], 1.1, 10.0, "TP1 x"),
    "trail": ("trail_pct", float, [10, 15, 20, 30], 5, 60, "Trailing %"),
    "size": ("position_size_sol", float, [0.02, 0.05, 0.1, 0.2], 0.005, 1.0, "Position Size SOL"),
    "maxpos": ("max_positions", int, [3, 5, 8, 12], 1, 20, "Max Positions"),
    "loss": ("daily_loss_limit_sol", float, [0.1, 0.3, 0.5, 1.0], 0.05, 5.0, "Daily Loss Limit SOL"),
}


def _btn(text, data):
    return {"text": text, "callback_data": data}


class TelegramUI:
    def __init__(self, label: str, s, pm, loop, chat_id: str, token: str):
        self.label, self.s, self.pm, self.loop, self.chat_id, self.token = label, s, pm, loop, chat_id, token
        self.http = httpx.Client(timeout=15.0)
        self.states: dict[int, dict] = {}  # chat_id -> per-chat state
        self.offset = 0
        self._db_path = s.db_url.split("///", 1)[-1] if "///" in s.db_url else s.db_url

    # ── low-level Bot API ──
    def _api(self, method: str, **kw) -> dict:
        try:
            r = self.http.post(API.format(token=self.token, method=method), json=kw)
            return r.json()
        except Exception as e:
            log.warning("tg api %s failed: %s", method, e)
            return {}

    def send(self, chat_id, text, kb=None) -> int | None:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if kb:
            payload["reply_markup"] = {"inline_keyboard": kb}
        data = self._api("sendMessage", **payload)
        return (data.get("result") or {}).get("message_id")

    def edit(self, chat_id, message_id, text, kb=None):
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
        if kb is not None:
            payload["reply_markup"] = {"inline_keyboard": kb}
        self._api("editMessageText", **payload)

    def answer(self, callback_id: str):
        self._api("answerCallbackQuery", callback_query_id=callback_id)

    def poll(self) -> list[dict]:
        try:
            r = self.http.get(API.format(token=self.token, method="getUpdates"),
                              params={"timeout": 20, "offset": self.offset})
            updates = (r.json().get("result") or [])
        except Exception as e:
            log.warning("tg getUpdates failed: %s", e)
            return []
        events = []
        for u in updates:
            self.offset = u["update_id"] + 1
            if (cb := u.get("callback_query")):
                msg = cb.get("message") or {}
                events.append({"type": "callback", "chat_id": msg.get("chat", {}).get("id"),
                               "message_id": msg.get("message_id"), "data": cb.get("data", ""),
                               "callback_id": cb["id"]})
            elif (m := u.get("message")) and m.get("text"):
                txt = m["text"].strip()
                if txt.startswith("/"):
                    events.append({"type": "command", "chat_id": m["chat"]["id"], "cmd": txt.split()[0].lower()})
                else:
                    events.append({"type": "text", "chat_id": m["chat"]["id"], "text": txt})
        return events

    # ── DB reads (sync, safe from this thread) ──
    def _q(self, sql: str):
        if not os.path.exists(self._db_path):
            return []
        try:
            con = sqlite3.connect(self._db_path)
            try:
                return con.execute(sql).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            return []

    def _open_positions(self):
        return self._q("SELECT mint, symbol, size_sol, entry_price, last_price, pnl_pct FROM positions"
                       " WHERE status='open' ORDER BY opened_at DESC LIMIT 20")

    def _recent_trades(self):
        return self._q("SELECT symbol, exit_reason, realized_pnl_sol, pnl_pct, closed_at FROM positions"
                       " WHERE status='closed' ORDER BY closed_at DESC LIMIT 10")

    def _counts(self):
        pos = self._q("SELECT COUNT(*) FROM positions WHERE status='open'")
        closed = self._q("SELECT COUNT(*), COALESCE(SUM(realized_pnl_sol),0), "
                         "COALESCE(SUM(CASE WHEN pnl_pct>0 THEN 1 ELSE 0 END),0) FROM positions WHERE status='closed'")
        n_open = pos[0][0] if pos else 0
        n_closed, tot_sol, wins = closed[0] if closed else (0, 0.0, 0)
        return n_open, n_closed, tot_sol, wins

    # ── renderers ──
    def home(self) -> tuple[str, list]:
        n_open, n_closed, tot_sol, wins = self._counts()
        wr = f"{wins}/{n_closed}" if n_closed else "0/0"
        paused = os.path.exists(self.s.kill_file)
        text = (f"<b>Dcrypt-{self.label}</b> [PAPER{' | PAUSED' if paused else ''}]\n"
                f"Open positions: {n_open}\nClosed trades: {n_closed} | Win rate: {wr}\n"
                f"Realized PnL: {tot_sol:+.4f} SOL\n"
                f"Position size: {self.s.position_size_sol} SOL | SL: {self.s.hard_stop_pct}% | TP1x: {self.s.tp1_mult}x")
        pause_btn = _btn("Resume", "resume") if paused else _btn("Pause", "pause")
        kb = [[_btn("Positions", "pos"), _btn("Trades", "trades")],
              [_btn("Settings", "settings"), _btn("Stats", "stats")],
              [pause_btn, _btn("Refresh", "home")]]
        return text, kb

    def positions(self) -> tuple[str, list]:
        rows = self._open_positions()
        if not rows:
            return "<b>Open Positions</b>\n\nNone.", [[_btn("Home", "home")]]
        lines = [f"<b>Open Positions ({len(rows)})</b>\n"]
        kb = []
        for mint, sym, size, entry, last, pnl in rows:
            pnl = pnl if pnl is not None else 0
            lines.append(f"• <b>${sym or mint[:6]}</b> {pnl:+.1f}% | {size:.3f} SOL | last {last}")
            kb.append([_btn(f"Sell {sym or mint[:6]} 50%", f"sell_{mint[:8]}_50"),
                       _btn(f"Sell {sym or mint[:6]} 100%", f"sell_{mint[:8]}_100")])
        kb.append([_btn("Sell All", "sellall"), _btn("Home", "home")])
        return "\n".join(lines), kb

    def trades(self) -> tuple[str, list]:
        rows = self._recent_trades()
        if not rows:
            return "<b>Recent Trades</b>\n\nNone.", [[_btn("Home", "home")]]
        lines = ["<b>Recent Trades</b>\n"]
        for sym, reason, sol, pct, closed in rows:
            sol = sol or 0
            pct = pct or 0
            lines.append(f"{'+' if sol >= 0 else ''}{sol:.4f} SOL ({pct:+.1f}%) ${sym or '?'} — {reason}")
        return "\n".join(lines), [[_btn("Home", "home")]]

    def stats(self) -> tuple[str, list]:
        n_open, n_closed, tot_sol, wins = self._counts()
        wr = f"{wins}/{n_closed}" if n_closed else "0/0"
        return (f"<b>Stats — Dcrypt-{self.label}</b>\n\nOpen: {n_open}\nClosed: {n_closed}\n"
                f"Realized PnL: {tot_sol:+.4f} SOL\nWin rate: {wr}"), [[_btn("Home", "home")]]

    def settings(self) -> tuple[str, list]:
        t = (f"<b>Settings</b>\n\nHard stop: {self.s.hard_stop_pct}%\nTP1x: {self.s.tp1_mult}\n"
             f"Trailing: {self.s.trail_pct}%\nSize: {self.s.position_size_sol} SOL\n"
             f"Max positions: {self.s.max_positions}\nDaily loss limit: {self.s.daily_loss_limit_sol} SOL")
        kb = [[_btn("Hard Stop", "set_hp"), _btn("TP1x", "set_tp")],
              [_btn("Trailing", "set_trail"), _btn("Size", "set_size")],
              [_btn("Max Pos", "set_maxpos"), _btn("Loss Limit", "set_loss")],
              [_btn("Home", "home")]]
        return t, kb

    def set_edit(self, key: str) -> tuple[str, list]:
        attr, typ, presets, mn, mx, label = SETTINGS[key]
        cur = getattr(self.s, attr)
        row = [_btn(str(p), f"sv_{key}_{p}") for p in presets]
        kb = [row, [_btn("Custom", f"cust_{key}"), _btn("Back", "settings")]]
        return f"<b>Set {label}</b> (current: {cur})\nRange {mn}–{mx}", kb

    # ── actions ──
    def apply_setting(self, key: str, raw: str) -> str:
        attr, typ, _, mn, mx, label = SETTINGS[key]
        try:
            val = typ(raw)
        except (ValueError, TypeError):
            return "Invalid value."
        if val < mn or val > mx:
            return f"Out of range ({mn}–{mx})."
        setattr(self.s, attr, val)
        log.info("setting changed via telegram: %s = %s", attr, val)
        return f"<b>{label}</b> updated to {val}"

    def force_sell_sync(self, mint8: str, pct: int) -> str:
        rows = self._open_positions()
        target = next((r for r in rows if r[0].startswith(mint8)), None)
        if not target:
            return "Position not found."
        try:
            fut = asyncio.run_coroutine_threadsafe(self.pm.force_sell(target[0], pct / 100), self.loop)
            return fut.result(timeout=30)
        except Exception as e:
            return f"Sell failed: {e}"

    def sell_all_sync(self) -> str:
        rows = self._open_positions()
        if not rows:
            return "No open positions."
        sold = 0
        for r in rows:
            try:
                asyncio.run_coroutine_threadsafe(self.pm.force_sell(r[0], 1.0), self.loop).result(timeout=30)
                sold += 1
            except Exception as e:
                log.warning("sellall error: %s", e)
        return f"Sell-all requested for {sold}/{len(rows)} positions."

    def set_paused(self, paused: bool) -> str:
        try:
            if paused:
                open(self.s.kill_file, "w").close()
                return "Paused. New entries blocked; exits keep running."
            if os.path.exists(self.s.kill_file):
                os.remove(self.s.kill_file)
            return "Resumed."
        except Exception as e:
            return f"Failed: {e}"

    # ── event handling ──
    def handle_event(self, ev: dict):
        chat_id = ev.get("chat_id")
        if str(chat_id) != str(self.chat_id):
            return
        st = self.states.setdefault(chat_id, {"awaiting": ""})

        if ev["type"] == "command":
            cmd = ev["cmd"]
            if cmd in ("/start", "/menu", "/status"):
                t, kb = self.home()
                self.send(chat_id, t, kb)
            elif cmd == "/positions":
                t, kb = self.positions()
                self.send(chat_id, t, kb)
            elif cmd == "/trades":
                t, kb = self.trades()
                self.send(chat_id, t, kb)
            elif cmd == "/stats":
                t, kb = self.stats()
                self.send(chat_id, t, kb)
            elif cmd == "/settings":
                t, kb = self.settings()
                self.send(chat_id, t, kb)
            elif cmd == "/pause":
                self.send(chat_id, self.set_paused(True))
            elif cmd == "/resume":
                self.send(chat_id, self.set_paused(False))
            else:
                self.send(chat_id, f"Unknown command. /start for menu. (Dcrypt-{self.label})")

        elif ev["type"] == "callback":
            self.answer(ev["callback_id"])
            mid = ev.get("message_id")
            data = ev.get("data", "")
            if data == "home":
                t, kb = self.home(); self.edit(chat_id, mid, t, kb)
            elif data == "pos":
                t, kb = self.positions(); self.edit(chat_id, mid, t, kb)
            elif data == "trades":
                t, kb = self.trades(); self.edit(chat_id, mid, t, kb)
            elif data == "stats":
                t, kb = self.stats(); self.edit(chat_id, mid, t, kb)
            elif data == "settings":
                t, kb = self.settings(); self.edit(chat_id, mid, t, kb)
            elif data == "pause":
                self.edit(chat_id, mid, self.set_paused(True), [[_btn("Home", "home")]])
            elif data == "resume":
                self.edit(chat_id, mid, self.set_paused(False), [[_btn("Home", "home")]])
            elif data.startswith("set_") and data[4:] in SETTINGS:
                t, kb = self.set_edit(data[4:]); self.edit(chat_id, mid, t, kb)
            elif data.startswith("sv_"):
                _, key, val = data.split("_", 2)
                if key in SETTINGS:
                    self.edit(chat_id, mid, self.apply_setting(key, val), [[_btn("Back", "settings"), _btn("Home", "home")]])
            elif data.startswith("cust_") and data[5:] in SETTINGS:
                st["awaiting"] = data[5:]
                self.edit(chat_id, mid, f"Send the new value for <b>{SETTINGS[data[5:]][5]}</b>:",
                          [[_btn("Cancel", "settings")]])
            elif data.startswith("sell_"):
                parts = data.split("_")
                if len(parts) == 3 and parts[2].isdigit():
                    self.edit(chat_id, mid, self.force_sell_sync(parts[1], int(parts[2])),
                              [[_btn("Positions", "pos"), _btn("Home", "home")]])
            elif data == "sellall":
                self.edit(chat_id, mid, "Sell ALL open positions at market?\n\n"
                          + self._q_count_line(), [[_btn("Yes, sell all", "sellall_yes"), _btn("Cancel", "pos")]])
            elif data == "sellall_yes":
                self.edit(chat_id, mid, self.sell_all_sync(), [[_btn("Home", "home")]])

        elif ev["type"] == "text":
            if st["awaiting"]:
                key = st["awaiting"]
                st["awaiting"] = ""
                if key in SETTINGS:
                    reply = self.apply_setting(key, ev["text"])
                else:
                    reply = "Cancelled."
                # send as new message since we lack the menu message id here
                self.send(chat_id, reply)

    def _q_count_line(self):
        n = self._q("SELECT COUNT(*) FROM positions WHERE status='open'")
        return f"{n[0][0] if n else 0} open position(s)."

    def run(self):
        log.info("telegram UI started (%s)", self.label)
        while True:
            try:
                for ev in self.poll():
                    self.handle_event(ev)
            except Exception:
                log.exception("telegram UI poll failed")
                import time
                time.sleep(5)


def start_telegram_ui(label: str, s, pm, loop, chat_id: str, token: str) -> threading.Thread:
    ui = TelegramUI(label, s, pm, loop, chat_id, token)
    t = threading.Thread(target=ui.run, daemon=True, name=f"tg-ui-{label}")
    t.start()
    return t
