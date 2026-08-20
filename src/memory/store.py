"""Local SQLite storage — schema owner and connection helper.

Single-process personal bot, tiny data volume: one SQLite file replaces
Postgres + Redis entirely. Each call opens a short-lived connection via
asyncio.to_thread rather than sharing one connection across the thread pool
or pulling in aiosqlite — simplest thing that works at this scale.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from loguru import logger

from src.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv_cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    channel_id TEXT PRIMARY KEY,
    messages TEXT NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_daily_price (
    symbol TEXT NOT NULL,
    company_name TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    close REAL, change_pct REAL, volume INTEGER,
    sma_20 REAL, sma_60 REAL, rsi_14 REAL,
    macd REAL, macd_signal REAL,
    bb_upper REAL, bb_lower REAL,
    bias_20 REAL, bias_60 REAL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS stock_daily_chip (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    foreign_net INTEGER, trust_net INTEGER, dealer_net INTEGER,
    total_3_institutions INTEGER,
    margin_buy_balance INTEGER, short_sell_balance INTEGER,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS stock_daily_fundamental (
    symbol TEXT NOT NULL,
    company_name TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    pe_ratio REAL, pb_ratio REAL, eps_ttm REAL, roe REAL,
    gross_margin REAL, revenue_growth REAL,
    analyst_target REAL, analyst_recommendation TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
);
"""


def _connect() -> sqlite3.Connection:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _create_schema() -> None:
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


async def init_storage() -> bool:
    """Create the SQLite file + schema if missing. Never raises."""
    try:
        await asyncio.to_thread(_create_schema)
        logger.info(f"Storage initialized at {settings.db_path}")
        return True
    except Exception as exc:
        logger.warning(f"Storage init failed: {exc}")
        return False
