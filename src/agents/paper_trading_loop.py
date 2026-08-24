"""Autonomous paper-trading loop -- runs as a background task inside the
same process as the Discord bot (see discord_bot.py's setup_hook, gated by
settings.paper_trading_enabled), watching the market during trading hours.

Candidate discovery is deterministic (same reasoning as daily_brief: this
runs unattended, so finding *what's worth looking at* must not depend on
the LLM remembering to check). The actual buy/sell/watch *decision* is
delegated to the same react agent (src/agents/research_agent.py) used for
free-form questions, via the paper_trade_status/buy/sell MCP tools --
Claude decides for itself which data (technical/fundamental/chip/MOPS/
news/...) it wants to check before acting, the same way it would for a
user-asked question. This was a deliberate choice after discussing the
trade-off: a hand-rolled fixed-data-then-decide prompt guarantees nothing
gets skipped but can't see anything outside a hardcoded bundle; the user
preferred richer, self-directed context over that guarantee.

Two fixed cadences, not LLM-self-paced (deliberate -- predictable cost,
still "checks more when it matters"):
- broad scan (every BROAD_SCAN_INTERVAL): news -> hot-stock discovery,
  same helpers daily_brief uses, to find new watchlist candidates.
- tight scan (every TIGHT_SCAN_INTERVAL, 4x more often): one run_research()
  call covering every due watchlist candidate and every open position.

The watchlist is in-memory only, not persisted -- it's not a committed
decision, and rebuilding it from a fresh broad scan after a restart is
fine. Only actual open/closed positions (src/memory/paper_trading_store.py)
are durable. Watchlist entries that are never bought expire after
WATCHLIST_TTL -- there's no explicit "drop" signal anymore since the
decision is free-form text, not parsed JSON, so stale candidates are
cleared mechanically instead of by LLM confirmation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone

from loguru import logger

from src.agents.daily_brief import _fetch_news
from src.agents.market_agent import _extract_hot_stocks
from src.agents.research_agent import run_research
from src.memory.paper_trading_store import get_open_positions
from src.memory.store import init_storage

_TW_TZ = timezone(timedelta(hours=8))
BROAD_SCAN_INTERVAL = 20 * 60
TIGHT_SCAN_INTERVAL = 5 * 60
WATCHLIST_TTL = 2 * 60 * 60
TICK_SECONDS = 60
_TRADING_START = time(9, 0)
_TRADING_END = time(13, 30)

# {symbol: {"first_seen": float, "last_checked": float}}
_watchlist: dict[str, dict] = {}


def _tw_now() -> datetime:
    return datetime.now(_TW_TZ)


def _is_trading_hours(now: datetime) -> bool:
    return now.weekday() < 5 and _TRADING_START <= now.time() <= _TRADING_END


def _next_session_start(now: datetime) -> datetime:
    candidate = now.replace(
        hour=_TRADING_START.hour, minute=_TRADING_START.minute, second=0, microsecond=0
    )
    if now.time() >= _TRADING_START:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


async def _broad_scan() -> None:
    logger.info("paper_trading_loop: broad scan (news -> hot stocks)")
    news_articles = await _fetch_news()
    candidates = await _extract_hot_stocks(news_articles)

    open_symbols = {p["symbol"] for p in await get_open_positions()}
    now_ts = _tw_now().timestamp()
    new_count = 0
    for symbol in candidates:
        if symbol in open_symbols or symbol in _watchlist:
            continue
        _watchlist[symbol] = {"first_seen": now_ts, "last_checked": 0.0}
        new_count += 1
    logger.info(
        f"paper_trading_loop: broad scan found {len(candidates)} candidates, "
        f"{new_count} new to watchlist"
    )


def _expire_stale_watchlist(open_symbols: set[str], now_ts: float) -> None:
    cutoff = now_ts - WATCHLIST_TTL
    for symbol in list(_watchlist.keys()):
        if symbol not in open_symbols and _watchlist[symbol]["first_seen"] < cutoff:
            del _watchlist[symbol]
            logger.info(f"paper_trading_loop: {symbol} expired off watchlist (never bought)")


async def _tight_scan() -> None:
    now_ts = _tw_now().timestamp()
    open_positions = await get_open_positions()
    open_symbols = {p["symbol"] for p in open_positions}

    _expire_stale_watchlist(open_symbols, now_ts)

    due_watchlist = [
        s for s, meta in _watchlist.items()
        if now_ts - meta["last_checked"] >= TIGHT_SCAN_INTERVAL
    ]
    if not due_watchlist and not open_positions:
        return

    logger.info(
        f"paper_trading_loop: tight scan — {len(due_watchlist)} watchlist due, "
        f"{len(open_positions)} open positions"
    )

    watchlist_lines = [f"- {s}" for s in due_watchlist] or ["（無）"]
    position_lines = [
        f"- {p['symbol']}（進場 {p['entry_price']}，{p['entry_date']}）" for p in open_positions
    ] or ["（無）"]

    prompt = (
        "現在是台股交易時段，這是紙上交易（模擬帳戶）的例行檢查。請檢查以下清單，"
        "自行判斷是否要對其中任何股票採取行動（買進、賣出、或都不動作）。"
        "務必實際呼叫工具查證真實數據後再決定，不要只憑下方名單文字判斷。\n\n"
        "觀察名單（尚未持有，值得留意的候選股）：\n" + "\n".join(watchlist_lines) + "\n\n"
        "目前持有中部位：\n" + "\n".join(position_lines)
    )

    try:
        result = await run_research(prompt, [])
        logger.info(f"paper_trading_loop: cycle conclusion — {result.get('conclusion', '')}")
    except Exception as exc:
        logger.warning(f"paper_trading_loop: research call failed: {exc}")
        return

    for symbol in due_watchlist:
        if symbol in _watchlist:
            _watchlist[symbol]["last_checked"] = now_ts
    # A watchlist symbol that got bought this cycle is now an open position;
    # drop it from the watchlist so it isn't asked about twice.
    new_open_symbols = {p["symbol"] for p in await get_open_positions()}
    for symbol in list(_watchlist.keys()):
        if symbol in new_open_symbols:
            del _watchlist[symbol]


async def run() -> None:
    await init_storage()
    logger.info(
        f"paper_trading_loop: starting "
        f"(broad={BROAD_SCAN_INTERVAL}s, tight={TIGHT_SCAN_INTERVAL}s, "
        f"trading hours {_TRADING_START}-{_TRADING_END} Asia/Taipei, weekdays)"
    )
    last_broad, last_tight = 0.0, 0.0

    while True:
        now = _tw_now()
        if not _is_trading_hours(now):
            resume_at = _next_session_start(now)
            sleep_s = max((resume_at - now).total_seconds(), TICK_SECONDS)
            logger.info(
                f"paper_trading_loop: outside trading hours, sleeping until {resume_at.isoformat()}"
            )
            await asyncio.sleep(sleep_s)
            continue

        now_ts = now.timestamp()
        try:
            if now_ts - last_broad >= BROAD_SCAN_INTERVAL:
                await _broad_scan()
                last_broad = now_ts
            if now_ts - last_tight >= TIGHT_SCAN_INTERVAL:
                await _tight_scan()
                last_tight = now_ts
        except Exception as exc:
            logger.error(f"paper_trading_loop: cycle error: {exc}", exc_info=True)

        await asyncio.sleep(TICK_SECONDS)
