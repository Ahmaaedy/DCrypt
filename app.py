"""Dcrypt Space host: runs both bot variants in threads and serves a Gradio UI.

DRY_RUN=true (default) -> UI only, no Telegram connection; DBs are read/tailed.
DRY_RUN=false -> start both pipelines (paper mode) in background threads.

Env/secrets: TG_API_ID, TG_API_HASH, TG_SESSION_STRING, TG_CHANNELS,
RPC_URL, DB_URL_PASTE, DB_URL_MT, NOTIFY_BOT_TOKEN, NOTIFY_CHAT_ID.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sqlite3
import sys
import threading

log = logging.getLogger("dcrypt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() in ("1", "true", "yes")
HERE = os.path.dirname(os.path.abspath(__file__))
VARIANTS = [
    ("paste", "dcrypt_paste", os.environ.get("DB_URL_PASTE", "sqlite+aiosqlite:///dcrypt_paste.db")),
    ("mt", "dcrypt_mt", os.environ.get("DB_URL_MT", "sqlite+aiosqlite:///dcrypt_mt.db")),
]


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


def _run_variant(variant_pkg: str, subdir: str, db_url: str) -> None:
    async def main():
        app = _load(variant_pkg + ".app", subdir)
        s = _settings(variant_pkg, db_url)
        await app.run(s)

    try:
        asyncio.run(main())
    except Exception:
        log.exception("variant %s crashed", variant_pkg)


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
    return _query(db_url,
                  "SELECT signal_id, mint, offset_s, price_native, liquidity_usd, txns_m5, volume_m5, txns_h1, volume_h1 "
                  "FROM price_snapshots ORDER BY id DESC LIMIT 50")


def build_ui():
    import gradio as gr

    blocks = gr.Blocks(title="Dcrypt")
    with blocks:
        gr.Markdown("# Dcrypt — signal executer (paste) + momentum trader (mt)")
        gr.Markdown(f"DRY_RUN={'on (no Telegram connection)' if DRY_RUN else 'off'}")
        for variant, pkg, db_url in VARIANTS:
            with gr.Tab(f"Dcrypt-{variant}"):
                refresh = gr.Button("Refresh")
                stats_out = gr.Textbox(label=f"Dcrypt-{variant} stats", lines=20)
                positions_df = gr.Dataframe(label="positions (latest 50)",
                                            headers=["id", "symbol", "mint", "status", "size_sol", "entry", "last", "pnl%", "exit", "opened"])
                decisions_df = gr.Dataframe(label="decisions (latest 30)",
                                            headers=["id", "signal_id", "accepted", "reason", "size_sol", "created"])
                snapshots_df = gr.Dataframe(label="snapshots (latest 50)",
                                            headers=["signal_id", "mint", "offset_s", "price", "liq_usd", "txns_m5", "vol_m5", "txns_h1", "vol_h1"])

                def _refresh(v=variant, u=db_url):
                    return (stats_text(v, u), positions_rows(u), decisions_rows(u), snapshots_rows(u))

                refresh.click(_refresh, None, [stats_out, positions_df, decisions_df, snapshots_df])
    return blocks


demo = build_ui()


def main() -> None:
    if not DRY_RUN:
        tg_required = ["TG_API_ID", "TG_API_HASH", "TG_CHANNELS"]
        missing = [k for k in tg_required if not os.environ.get(k)]
        if missing:
            raise SystemExit(f"Missing required env/secrets: {missing}")
        for variant, pkg, db_url in VARIANTS:
            t = threading.Thread(target=_run_variant, args=(pkg, pkg, db_url), daemon=True)
            t.start()
            log.info("started variant %s", variant)
    demo.launch()


if __name__ == "__main__":
    main()
