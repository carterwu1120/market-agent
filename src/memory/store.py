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

-- Permanent audit log: unlike `sessions` (a capped, expiring rolling window
-- used as LLM context), every turn written here never expires and is never
-- trimmed. `sessions` losing history after 20 messages / 1hr TTL is by
-- design (context window sizing); this table exists so that loss doesn't
-- also mean the conversation is gone forever.
CREATE TABLE IF NOT EXISTS conversation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_conversation_log_channel
    ON conversation_log (channel_id, created_at);

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

-- Paper-trading tracker (src/agents/paper_trading_loop.py): a real open ->
-- closed position lifecycle, not a one-shot recommendation. entry_price/
-- exit_price always come from real fetched prices, never an LLM-stated
-- number, same principle as every other table here. No real or
-- third-party trading account involved -- see docs/adr for why (CMoney's
-- virtual-trading login migrated to OIDC and the only unofficial client
-- library for it is dead).
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,              -- open | closed
    horizon TEXT NOT NULL DEFAULT 'short_term',  -- short_term | long_term
    shares INTEGER NOT NULL DEFAULT 0,
    allocation_amount REAL NOT NULL DEFAULT 0,  -- capital committed at entry (shares * entry_price)
    entry_price REAL NOT NULL,
    entry_date TEXT NOT NULL,
    entry_reason TEXT NOT NULL DEFAULT '',
    exit_price REAL,
    exit_date TEXT,
    exit_reason TEXT NOT NULL DEFAULT '',   -- take_profit | stop_loss | llm_signal
    created_at REAL NOT NULL,
    closed_at REAL
);

CREATE INDEX IF NOT EXISTS ix_paper_positions_symbol_status ON paper_positions (symbol, status);

-- Watchlist: candidates spotted by the broad scan but not yet bought.
-- Persisted (unlike an earlier in-memory-only version) because
-- paper_trading_loop.py and the MCP tool subprocess (mcp_server.py, spawned
-- fresh per claude -p call) are different OS processes -- an agent-driven
-- watchlist_drop tool call can only affect state that both sides can see,
-- which means the database, not a Python dict in one process's memory.
CREATE TABLE IF NOT EXISTS paper_watchlist (
    symbol TEXT PRIMARY KEY,
    first_seen REAL NOT NULL,
    last_checked REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT ''
);

-- Agent-set conditional orders: the agent analyzes a symbol once and picks
-- an indicator/threshold ("buy if close < 550", "sell if rsi_14 > 70")
-- instead of paying for a fresh run_research() call every tight-scan cycle
-- just to re-ask the same question. paper_trading_loop.py's
-- _check_conditions() evaluates these every tick against real fetched
-- data (no LLM call) and executes buy()/sell() directly the moment one
-- triggers -- the agent still decided the indicator/threshold/action, the
-- mechanical part is only "keep checking until it's true".
CREATE TABLE IF NOT EXISTS paper_trade_conditions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    indicator TEXT NOT NULL,       -- close | sma_20 | sma_60 | rsi_14 | macd | macd_signal |
                                    -- macd_hist | bb_upper | bb_lower | ema_12 | bias_20 | bias_60
    operator TEXT NOT NULL,        -- lt | gt | lte | gte
    threshold REAL NOT NULL,
    action TEXT NOT NULL,          -- buy | sell
    horizon TEXT NOT NULL DEFAULT 'short_term',      -- used when action = buy
    allocation_pct REAL NOT NULL DEFAULT 10.0,       -- used when action = buy
    exit_reason TEXT NOT NULL DEFAULT 'llm_signal',  -- used when action = sell
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',  -- active | triggered | cancelled
    created_at REAL NOT NULL,
    triggered_at REAL
);

CREATE INDEX IF NOT EXISTS ix_paper_conditions_status ON paper_trade_conditions (status);

-- Permanent audit log for the paper-trading loop -- same "never expires,
-- never trimmed" spirit as conversation_log above, but for what the loop
-- itself did (broad/tight scans, mechanical triggers, trades, condition
-- changes) rather than chat turns. Answers "what happened between 2pm and
-- 3pm" by querying this table directly instead of grepping loguru output,
-- which isn't structured or guaranteed to still be on disk.
CREATE TABLE IF NOT EXISTS paper_trading_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    event_type TEXT NOT NULL,  -- broad_scan | tight_scan | long_term_review |
                                -- stop_loss_triggered | condition_triggered |
                                -- condition_set | condition_cancelled | buy | sell |
                                -- budget_exceeded
    symbol TEXT,               -- nullable -- not every event is about one symbol
    detail TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_paper_trading_log_ts ON paper_trading_log (ts);
"""


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Adds columns to tables that already existed before this column was
    introduced. CREATE TABLE IF NOT EXISTS above only applies to fresh
    databases -- an existing paper_positions table (already holding real
    rows from earlier live testing) needs an explicit ALTER TABLE."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(paper_positions)").fetchall()}
    if "horizon" not in cols:
        conn.execute(
            "ALTER TABLE paper_positions ADD COLUMN horizon TEXT NOT NULL DEFAULT 'short_term'"
        )
        conn.commit()
    if "shares" not in cols:
        conn.execute("ALTER TABLE paper_positions ADD COLUMN shares INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    if "allocation_amount" not in cols:
        conn.execute(
            "ALTER TABLE paper_positions ADD COLUMN allocation_amount REAL NOT NULL DEFAULT 0"
        )
        conn.commit()


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
        _migrate_schema(conn)
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
