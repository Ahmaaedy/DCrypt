"""Dcrypt Space host: runs both bot variants in threads and serves a Gradio UI.

DRY_RUN=true (default) -> UI only, no Telegram connection; DBs are read/tailed.
DRY_RUN=false -> start both pipelines (paper mode) in background threads.

Env/secrets: TG_API_ID, TG_API_HASH, TG_SESSION_STRING, TG_CHANNELS,
RPC_URL, DB_URL_PASTE, DB_URL_MT, NOTIFY_BOT_TOKEN, NOTIFY_CHAT_ID.
"""
from __future__ import annotations

import asyncio
import collections
import importlib
import logging
import os
import sqlite3
import sys
import threading

log = logging.getLogger("dcrypt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_log_lines: collections.deque = collections.deque(maxlen=150)


class _GradioLogHandler(logging.Handler):
    def emit(self, record):
        try:
            _log_lines.append(self.format(record))
        except Exception:
            pass


def _setup_log_capture():
    h = _GradioLogHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logging.getLogger().addHandler(h)
    for noisy in ("httpx", "httpcore", "websockets", "urllib3", "gradio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_setup_log_capture()

# ZeroGPU Spaces require at least one @spaces.GPU-decorated function to boot.
try:
    import spaces

    @spaces.GPU
    def _noop():
        return None
except Exception:
    pass

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() in ("1", "true", "yes")
HERE = os.path.dirname(os.path.abspath(__file__))
VARIANTS = [
    ("paste", "dcrypt_paste", os.environ.get("DB_URL_PASTE", "sqlite+aiosqlite:///dcrypt_paste.db")),
    ("mt", "dcrypt_mt", os.environ.get("DB_URL_MT", "sqlite+aiosqlite:///dcrypt_mt.db")),
]

# ── Bot lifecycle state ──────────────────────────────────────
_threads: dict[str, threading.Thread] = {}
_tg_tokens_used: set[str] = set()
_stops: dict[str, threading.Event] = {}
_status: dict[str, str] = {v: "stopped" for v, _, _ in VARIANTS}
_config_errors: list[str] = []


def _load(variant_pkg: str, subdir: str):
    root = os.path.join(HERE, subdir)
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module(variant_pkg)


def _settings(variant_pkg: str, db_url: str):
    cfg = _load(variant_pkg + ".config", variant_pkg)
    return cfg.Settings(
        db_url=db_url,
        mode="paper",
        llm_backend=os.environ.get("LLM_BACKEND", "heuristic"),
        tg_session_string=os.environ.get("TG_SESSION_STRING", ""),
        rpc_url=os.environ.get("RPC_URL", ""),
        tg_channels=os.environ.get("TG_CHANNELS", ""),
        _env_file=None,
    )


def _run_variant(variant: str, variant_pkg: str, db_url: str, stop_event: threading.Event) -> None:
    async def main():
        app = _load(variant_pkg + ".app", variant_pkg)
        s = _settings(variant_pkg, db_url)

        def _ready(repo, prices, positions, pipeline):
            global _tg_tokens_used
            chat = os.environ.get("NOTIFY_CHAT_ID", "")
            token = os.environ.get(f"NOTIFY_BOT_TOKEN_{variant.upper()}") or os.environ.get("NOTIFY_BOT_TOKEN", "")
            if not (token and chat) or DRY_RUN:
                return
            if token in _tg_tokens_used:
                log.warning("NOTIFY_BOT_TOKEN shared between variants; skipping extra TG UI for %s", variant)
                return
            _tg_tokens_used.add(token)
            from telegram_ui import start_telegram_ui
            loop = asyncio.get_running_loop()
            start_telegram_ui(variant, s, positions, loop, chat, token)

        await app.run(s, stop_event=stop_event, on_ready=_ready)

    try:
        asyncio.run(main())
    except Exception as e:
        log.exception("variant %s crashed", variant_pkg)
        _status[variant] = f"crashed: {e}"
    else:
        _status[variant] = "stopped"


def start_variant(variant: str, pkg: str, db_url: str) -> str:
    if _threads.get(variant) and _threads[variant].is_alive():
        return f"Dcrypt-{variant} already running."
    missing = [k for k in ("TG_API_ID", "TG_API_HASH", "TG_CHANNELS") if not os.environ.get(k)]
    if missing:
        return f"Config errors: missing secrets {missing}; set them in Space settings."
    stop = threading.Event()
    _stops[variant] = stop
    t = threading.Thread(target=_run_variant, args=(variant, pkg, db_url, stop), daemon=True, name=f"dcrypt-{variant}")
    _threads[variant] = t
    _status[variant] = "running"
    t.start()
    return f"Dcrypt-{variant} started (paper mode)."


def stop_variant(variant: str) -> str:
    ev = _stops.get(variant)
    t = _threads.get(variant)
    if not t or not t.is_alive():
        _status[variant] = "stopped"
        return f"Dcrypt-{variant} is not running."
    if ev:
        ev.set()
    _status[variant] = "stopping"
    return f"Dcrypt-{variant} stopping..."


# ── DB readers ───────────────────────────────────────────────
def _db_path(db_url: str) -> str:
    return db_url.split("///", 1)[-1] if "///" in db_url else db_url


def _query(db_url: str, sql: str, params=()):
    path = _db_path(db_url)
    if not os.path.exists(path):
        return []
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        return [(f"error: {e}",)]
    finally:
        con.close()


def stats_text(variant: str, db_url: str) -> str:
    async def _go():
        db_mod = _load(f"dcrypt_{variant}.db", f"dcrypt_{variant}")
        stats_mod = _load(f"dcrypt_{variant}.stats", f"dcrypt_{variant}")
        db = db_mod.Database(db_url)
        await db.init()
        try:
            return await stats_mod.report(db)
        finally:
            await db.close()

    try:
        return asyncio.run(_go())
    except Exception as e:
        return f"error: {e}"


def positions_rows(db_url: str):
    return _query(db_url,
                  "SELECT id, symbol, mint, status, size_sol, entry_price, last_price, pnl_pct, exit_reason, opened_at "
                  "FROM positions ORDER BY id DESC LIMIT 50")


def decisions_rows(db_url: str):
    return _query(db_url,
                  "SELECT id, signal_id, accepted, reason, size_sol, created_at FROM decisions ORDER BY id DESC LIMIT 30")


def snapshots_rows(db_url: str):
    path = _db_path(db_url)
    cols = _query(db_url, "PRAGMA table_info(price_snapshots)")
    have = {c[1] for c in cols} if cols and cols[0] and isinstance(cols[0][0], int) else set()
    base = ["signal_id", "mint", "offset_s", "price_native", "liquidity_usd"]
    extra = [c for c in ("txns_m5", "volume_m5", "txns_h1", "volume_h1") if c in have]
    try:
        rows = _query(db_url, f"SELECT {', '.join(base + extra)} FROM price_snapshots ORDER BY id DESC LIMIT 50")
    except Exception:
        rows = []
    if not extra:
        rows = [tuple(r) + (None,) * 4 for r in rows]
    return rows


# ── Gradio UI ────────────────────────────────────────────────
def build_ui():
    import gradio as gr

    blocks = gr.Blocks(title="Dcrypt")
    with blocks:
        gr.Markdown("# Dcrypt — signal executer (paste) + momentum trader (mt)")
        gr.Markdown(f"DRY_RUN={'on (no Telegram connection)' if DRY_RUN else 'off — bots can be started below'}")
        config_box = gr.Textbox(label="config", value="; ".join(_config_errors) if _config_errors else "config ok",
                                interactive=False)

        outputs = []  # flat list for the auto-refresh timer
        for variant, pkg, db_url in VARIANTS:
            with gr.Tab(f"Dcrypt-{variant}"):
                with gr.Row():
                    start_btn = gr.Button("Start Bot", variant="primary")
                    stop_btn = gr.Button("Stop Bot", variant="stop")
                    status = gr.Textbox(label="status", value=_status[variant], interactive=False)
                stats_out = gr.Textbox(label=f"Dcrypt-{variant} stats", lines=20)
                positions_df = gr.Dataframe(label="positions (latest 50)",
                                            headers=["id", "symbol", "mint", "status", "size_sol", "entry", "last", "pnl%", "exit", "opened"])
                decisions_df = gr.Dataframe(label="decisions (latest 30)",
                                            headers=["id", "signal_id", "accepted", "reason", "size_sol", "created"])
                snapshots_df = gr.Dataframe(label="snapshots (latest 50)",
                                            headers=["signal_id", "mint", "offset_s", "price", "liq_usd", "txns_m5", "vol_m5", "txns_h1", "vol_h1"])
                outputs += [stats_out, positions_df, decisions_df, snapshots_df]

                start_btn.click(lambda v=variant, p=pkg, u=db_url: start_variant(v, p, u), None, status)
                stop_btn.click(lambda v=variant: stop_variant(v), None, status)

        with gr.Tab("Live Logs"):
            log_box = gr.Textbox(label="Logs", lines=20, interactive=False)

        def _refresh_all():
            out = []
            for variant, pkg, db_url in VARIANTS:
                out += [stats_text(variant, db_url), positions_rows(db_url), decisions_rows(db_url), snapshots_rows(db_url)]
            out.append("\n".join(_log_lines))
            return out

        timer = gr.Timer(value=5)
        timer.tick(_refresh_all, None, outputs + [log_box])
    return blocks


demo = build_ui()


def main() -> None:
    if not DRY_RUN:
        missing = [k for k in ("TG_API_ID", "TG_API_HASH", "TG_CHANNELS") if not os.environ.get(k)]
        if missing:
            _config_errors.append(f"missing secrets: {missing}")
            log.error("missing secrets: %s — bots will not auto-start", missing)
        else:
            for variant, pkg, db_url in VARIANTS:
                start_variant(variant, pkg, db_url)
    demo.launch(ssr_mode=False)


if __name__ == "__main__":
    main()
