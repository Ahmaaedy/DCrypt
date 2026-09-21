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


# ── Database Queries ──────────────────────────────────
