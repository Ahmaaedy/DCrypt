"""
Dcrypt Signal Summary Bot — Standalone hourly report via Gemini + Telegram.

Reads from bot_state.db, summarizes with Gemini, pushes to Telegram.
Run: python summary_bot.py
"""

import sqlite3
import asyncio
import time
import os
import logging
from datetime import datetime

import httpx
from telethon import TelegramClient, events
from google import genai

# ── Config ──────────────────────────────────────────────────
GEMINI_API_KEY = ""
TELEGRAM_API_ID = 31502565
TELEGRAM_API_HASH = ""
TELEGRAM_TARGET = "+2348100768563"            
DB_PATH = os.path.join(os.path.dirname(__file__), "dcrypt", "src", "bot_state.db")
REPORT_INTERVAL = 216000            # seconds (1 hour)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("summary")
START_TIME = time.time()

tg_client = TelegramClient(
    "summary_session",
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH,
    auto_reconnect=True,
    retry_delay=5,
    request_retries=100000,
    connection_retries=100000,
) if TELEGRAM_API_ID and TELEGRAM_API_HASH else None


# ── Database Queries ────────────────────────────────────────

def query_signal_sources(conn: sqlite3.Connection, since: float) -> list[dict]:
    """Signal source win rate and avg P&L."""
    rows = conn.execute("""
        SELECT source,
               COUNT(*) as total,
               SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
               ROUND(AVG(pnl_pct), 2) as avg_pnl,
               ROUND(AVG(confidence), 3) as avg_conf
        FROM signal_log
        WHERE created_at > ? AND resolved_at IS NOT NULL
        GROUP BY source
        ORDER BY total DESC
    """, (since,)).fetchall()
    return [dict(r) for r in rows]


def query_strategies(conn: sqlite3.Connection, since: float) -> list[dict]:
    """Strategy performance breakdown."""
    rows = conn.execute("""
        SELECT strategy,
               COUNT(*) as total,
               SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
               ROUND(AVG(pnl_pct), 2) as avg_pnl
        FROM signal_log
        WHERE created_at > ? AND resolved_at IS NOT NULL
        GROUP BY strategy
        ORDER BY total DESC
    """, (since,)).fetchall()
    return [dict(r) for r in rows]


def query_confidence(conn: sqlite3.Connection, since: float) -> list[dict]:
    """Does higher confidence correlate with better trades?"""
    rows = conn.execute("""
        SELECT
            CASE
                WHEN confidence >= 0.8 THEN 'high (0.8+)'
                WHEN confidence >= 0.5 THEN 'medium (0.5-0.8)'
                ELSE 'low (0-0.5)'
            END as bucket,
            COUNT(*) as total,
            SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
            ROUND(AVG(pnl_pct), 2) as avg_pnl
        FROM signal_log
        WHERE created_at > ? AND resolved_at IS NOT NULL
        GROUP BY bucket
        ORDER BY bucket DESC
    """, (since,)).fetchall()
    return [dict(r) for r in rows]


def query_recent_trades(conn: sqlite3.Connection, since: float) -> list[dict]:
    """Recent trade actions (for hourly report, keeps LIMIT 20)."""
    rows = conn.execute("""
        SELECT symbol, strategy, action, ROUND(sol_amount, 4) as sol,
               ROUND(pnl_sol, 4) as pnl, ROUND(pnl_pct, 1) as pnl_pct,
               reason, datetime(timestamp, 'unixepoch', 'localtime') as time
        FROM trades
        WHERE timestamp > ?
        ORDER BY timestamp DESC
        LIMIT 20
    """, (since,)).fetchall()
    return [dict(r) for r in rows]


def query_session_start(conn: sqlite3.Connection) -> float | None:
    """Get earliest trade timestamp as session start."""
    row = conn.execute(
        "SELECT MIN(timestamp) as ts FROM trades"
    ).fetchone()
    return row["ts"] if row and row["ts"] else None


def query_all_session_trades(conn: sqlite3.Connection, since: float) -> list[dict]:
    """All trades in session, no limit."""
    rows = conn.execute("""
        SELECT symbol, strategy, action, ROUND(sol_amount, 4) as sol,
               ROUND(pnl_sol, 4) as pnl, ROUND(pnl_pct, 1) as pnl_pct,
               reason, tx_signature,
               datetime(timestamp, 'unixepoch', 'localtime') as time
        FROM trades
        WHERE timestamp > ?
        ORDER BY timestamp ASC
    """, (since,)).fetchall()
    return [dict(r) for r in rows]


def query_session_by_strategy(conn: sqlite3.Connection, since: float) -> list[dict]:
    """Per-strategy performance breakdown for the session."""
    rows = conn.execute("""
        SELECT strategy,
               COUNT(*) as total,
               SUM(CASE WHEN action = 'sell' THEN 1 ELSE 0 END) as sells,
               SUM(CASE WHEN action = 'sell' AND pnl_sol > 0 THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN action = 'sell' AND pnl_sol <= 0 THEN 1 ELSE 0 END) as losses,
               ROUND(COALESCE(SUM(CASE WHEN action = 'sell' THEN pnl_sol ELSE 0 END), 0), 4) as total_pnl,
               ROUND(COALESCE(AVG(CASE WHEN action = 'sell' THEN pnl_pct END), 0), 1) as avg_pnl_pct
        FROM trades
        WHERE timestamp > ?
        GROUP BY strategy
        ORDER BY total_pnl DESC
    """, (since,)).fetchall()
    return [dict(r) for r in rows]


def query_session_overview(conn: sqlite3.Connection, since: float) -> dict:
    """Session-level summary stats."""
    row = conn.execute("""
        SELECT COUNT(*) as total_trades,
               SUM(CASE WHEN action = 'sell' THEN 1 ELSE 0 END) as sells,
               SUM(CASE WHEN action = 'sell' AND pnl_sol > 0 THEN 1 ELSE 0 END) as wins,
               ROUND(COALESCE(SUM(CASE WHEN action = 'sell' THEN pnl_sol ELSE 0 END), 0), 4) as total_pnl
        FROM trades
        WHERE timestamp > ?
    """, (since,)).fetchone()
    return dict(row) if row else {"total_trades": 0, "sells": 0, "wins": 0, "total_pnl": 0}


def query_open_positions(conn: sqlite3.Connection) -> list[dict]:
    """Currently open positions."""
    rows = conn.execute("""
        SELECT symbol, strategy, ROUND(sol_invested, 4) as sol,
               ROUND((strftime('%s', 'now') - entry_time) / 60.0, 1) as held_min
        FROM positions
        ORDER BY entry_time DESC
    """).fetchall()
    return [dict(r) for r in rows]


def query_daily_pnl(conn: sqlite3.Connection) -> dict:
    """Today's total P&L."""
    today_start = time.time() - (time.time() % 86400)
    row = conn.execute("""
        SELECT COALESCE(ROUND(SUM(pnl_sol), 4), 0) as total_pnl,
               COUNT(*) as trades
        FROM trades
        WHERE action = 'sell' AND timestamp > ?
    """, (today_start,)).fetchone()
    return dict(row) if row else {"total_pnl": 0, "trades": 0}


# ── Data Collection ─────────────────────────────────────────

def collect_data(hours_back: float = 1.0) -> str:
    """Run all queries and format into a text block for Gemini."""
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        return f"ERROR: Could not open database: {e}"

    since = time.time() - (hours_back * 3600)
    parts = []

    # 1. Signal sources
    sources = query_signal_sources(conn, since)
    if sources:
        parts.append("=== SIGNAL SOURCE PERFORMANCE ===")
        for s in sources:
            wr = round(s["wins"] / s["total"] * 100) if s["total"] > 0 else 0
            parts.append(
                f"  {s['source']}: {s['total']} trades, "
                f"{wr}% win rate, avg P&L {s['avg_pnl']}%, "
                f"avg confidence {s['avg_conf']}"
            )
    else:
        parts.append("=== SIGNAL SOURCE PERFORMANCE === (no resolved signals)")

    # 2. Strategy performance
    strategies = query_strategies(conn, since)
    if strategies:
        parts.append("\n=== STRATEGY PERFORMANCE ===")
        for s in strategies:
            wr = round(s["wins"] / s["total"] * 100) if s["total"] > 0 else 0
            parts.append(
                f"  {s['strategy']}: {s['total']} trades, "
                f"{wr}% win rate, avg P&L {s['avg_pnl']}%"
            )
    else:
        parts.append("\n=== STRATEGY PERFORMANCE === (no resolved signals)")

    # 3. Confidence correlation
    conf = query_confidence(conn, since)
    if conf:
        parts.append("\n=== CONFIDENCE CORRELATION ===")
        for c in conf:
            wr = round(c["wins"] / c["total"] * 100) if c["total"] > 0 else 0
            parts.append(
                f"  {c['bucket']}: {c['total']} trades, "
                f"{wr}% win rate, avg P&L {c['avg_pnl']}%"
            )

    # 4. Recent trades
    trades = query_recent_trades(conn, since)
    if trades:
        parts.append("\n=== RECENT TRADES ===")
        for t in trades[:10]:
            emoji = "+" if (t["pnl"] or 0) >= 0 else ""
            parts.append(
                f"  {t['time']} | {t['symbol']} | {t['action']} | "
                f"{t['sol']} SOL | {emoji}{t['pnl']} SOL ({emoji}{t['pnl_pct']}%) | "
                f"{t['reason']}"
            )
    else:
        parts.append("\n=== RECENT TRADES === (none in last hour)")

    # 5. Open positions
    positions = query_open_positions(conn)
    if positions:
        parts.append("\n=== OPEN POSITIONS ===")
        for p in positions:
            parts.append(
                f"  {p['symbol']} | {p['strategy']} | "
                f"{p['sol']} SOL | held {p['held_min']}m"
            )
    else:
        parts.append("\n=== OPEN POSITIONS === (none)")

    # 6. Daily P&L
    daily = query_daily_pnl(conn)
    parts.append(f"\n=== DAILY P&L ===")
    parts.append(f"  Total: {daily['total_pnl']} SOL from {daily['trades']} trades")

    conn.close()
    return "\n".join(parts)


# ── Gemini Summarization ───────────────────────────────────

SYSTEM_PROMPT = """You are a crypto memecoin trading analyst. You receive raw hourly
trading data from a Solana memecoin bot. Summarize it into a concise,
actionable report.


Include:
1. Overall performance summary (one line)
2. Which signal sources performed best and worst
3. Which strategies performed best and worst
4. Confidence calibration assessment (does higher confidence = better trades?)
5. Notable trades (big wins or losses)
6. Open position risk assessment
7. One-line recommendation for the next hour

Keep it under 400 words. Use plain text with bullet points. Be direct
and data-driven. If there's no data, say so clearly."""


async def gemini_summarize(data: str) -> str:
    """Send data to Gemini and return the summary."""
    if not GEMINI_API_KEY:
        return f"[Gemini disabled — raw data below]\n\n{data}"

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.0-flash",
            contents=f"{SYSTEM_PROMPT}\n\nCurrent time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{data}",
        )
        return response.text
    except Exception as e:
        log.error(f"Gemini error: {e}")
        return f"[Gemini error — raw data below]\n\n{data}"


# ── Telegram Delivery ──────────────────────────────────────

async def send_telegram(text: str):
    """Send a message via Telethon."""
    if not tg_client:
        log.warning("Telegram not configured — printing to console")
        print(text)
        return

    # Split at 4000 chars to stay under Telegram's 4096 limit
    chunks = []
    while len(text) > 4000:
        split_at = text.rfind("\n", 0, 4000)
        if split_at == -1:
            split_at = 4000
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    chunks.append(text)

    for i, chunk in enumerate(chunks):
        try:
            await tg_client.send_message(TELEGRAM_TARGET, chunk)
            log.info(f"Telegram sent ({i+1}/{len(chunks)}, {len(chunk)} chars)")
        except Exception as e:
            log.error(f"Telegram send failed: {e}")
        if i < len(chunks) - 1:
            await asyncio.sleep(1)


# ── Ping Handler ────────────────────────────────────────────

def build_status_message() -> str:
    uptime_sec = time.time() - START_TIME
    hours, remainder = divmod(int(uptime_sec), 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"

    db_ok = False
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.execute("SELECT 1")
        db_ok = True
        conn.close()
    except Exception:
        pass

    tg_ok = tg_client is not None and tg_client.is_connected()

    return (
        f"Pong!\n\n"
        f"Uptime: {uptime_str}\n"
        f"DB: {'OK' if db_ok else 'DOWN'}\n"
        f"Telegram: {'OK' if tg_ok else 'DOWN'}\n"
        f"Report interval: {REPORT_INTERVAL}s\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


async def handle_ping(event):
    await event.respond(build_status_message())


async def handle_dump(event):
    """Send detailed session dump with all trades."""
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        await send_telegram(f"ERROR: Could not open database: {e}")
        return

    session_start = query_session_start(conn)
    if not session_start:
        await send_telegram("No trades found in database.")
        conn.close()
        return

    now = time.time()
    session_hours = (now - session_start) / 3600
    h, rem = divmod(int(session_hours * 3600), 3600)
    m, _ = divmod(rem, 60)
    duration_str = f"{h}h {m}m" if h > 0 else f"{m}m"

    parts = []
    parts.append(f"Dcrypt Session Dump — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    parts.append(f"Session: {duration_str} | Since: {datetime.fromtimestamp(session_start).strftime('%Y-%m-%d %H:%M')}")
    parts.append("=" * 40)

    # 1. Session overview
    overview = query_session_overview(conn, session_start)
    win_rate = round(overview["wins"] / overview["sells"] * 100) if overview["sells"] > 0 else 0
    parts.append(f"\n=== SESSION OVERVIEW ===")
    parts.append(f"  Duration: {duration_str}")
    parts.append(f"  Total trades: {overview['total_trades']} | Sells: {overview['sells']} | Win rate: {win_rate}%")
    parts.append(f"  Total PnL: {overview['total_pnl']:+.4f} SOL")

    # 2. Open positions
    positions = query_open_positions(conn)
    parts.append(f"\n  Active positions: {len(positions)}")
    if positions:
        total_invested = sum(p["sol"] for p in positions)
        parts.append(f"  Total invested: {total_invested:.4f} SOL")

    # 3. Performance by strategy
    strategies = query_session_by_strategy(conn, session_start)
    if strategies:
        parts.append(f"\n=== PERFORMANCE BY STRATEGY ===")
        for s in strategies:
            wr = round(s["wins"] / s["sells"] * 100) if s["sells"] > 0 else 0
            parts.append(
                f"  {s['strategy']:14s} | {s['total']:3d} trades | "
                f"{wr:3d}% win | {s['total_pnl']:+.4f} SOL | avg {s['avg_pnl_pct']:+.1f}%"
            )

    # 4. All session trades (no limit)
    trades = query_all_session_trades(conn, session_start)
    if trades:
        parts.append(f"\n=== ALL SESSION TRADES ({len(trades)}) ===")
        for t in trades:
            emoji = "+" if (t["pnl"] or 0) >= 0 else ""
            pnl_str = f"{emoji}{t['pnl']:.4f} ({emoji}{t['pnl_pct']:.1f}%)" if t["action"] == "sell" and t["pnl"] is not None else "--"
            tx_short = t["tx_signature"][:12] + "..." if t.get("tx_signature") else ""
            parts.append(
                f"  {t['time']} | {t['symbol']:10s} | {t['strategy']:12s} | "
                f"{t['action']:4s} | {t['sol']:.4f} SOL | {pnl_str} | {t['reason']}"
            )
    else:
        parts.append(f"\n=== ALL SESSION TRADES === (none)")

    # 5. Open positions detail
    if positions:
        parts.append(f"\n=== OPEN POSITIONS ===")
        for p in positions:
            held = p["held_min"]
            if held >= 60:
                held_str = f"{held/60:.1f}h"
            else:
                held_str = f"{held:.0f}m"
            parts.append(
                f"  {p['symbol']:10s} | {p['strategy']:12s} | "
                f"{p['sol']:.4f} SOL | held {held_str}"
            )

    # 6. Daily P&L
    daily = query_daily_pnl(conn)
    parts.append(f"\n=== DAILY P&L ===")
    parts.append(f"  Total: {daily['total_pnl']:+.4f} SOL from {daily['trades']} sells")

    conn.close()

    header = "\n".join(parts)

    # Split if too long for one Telegram message
    await send_telegram(header)


# ── Main Loop ──────────────────────────────────────────────

async def run_once():
    """Collect data, summarize, send."""
    log.info("Collecting data...")
    data = collect_data(hours_back=1.0)

    if "ERROR" in data:
        log.error(data)
        return

    log.info("Sending to Gemini...")
    summary = await gemini_summarize(data)

    log.info("Sending to Telegram...")
    header = f"Dcrypt Hourly Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n{'='*40}\n"
    await send_telegram(header + summary)
    log.info("Done")


async def main():
    log.info("Summary bot starting...")
    log.info(f"DB: {DB_PATH}")
    log.info(f"Report interval: {REPORT_INTERVAL}s")

    # Start Telethon client with retries
    if tg_client:
        for attempt in range(100000):
            try:
                await tg_client.start()
                tg_client.add_event_handler(handle_ping, events.NewMessage(pattern='(?i)^ping$'))
                tg_client.add_event_handler(handle_dump, events.NewMessage(pattern='(?i)^dump$'))
                log.info("Telegram connected — handlers registered")
                break
            except Exception as e:
                log.error(f"Telegram connection attempt {attempt+1} failed: {e}")
                await asyncio.sleep(5)

    # Send first report immediately
    await run_once()

    # Then repeat every hour
    while True:
        await asyncio.sleep(REPORT_INTERVAL)
        try:
            await run_once()
        except Exception as e:
            log.error(f"Report cycle error: {e}")

    await tg_client.disconnect() if tg_client else None


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped by user")
