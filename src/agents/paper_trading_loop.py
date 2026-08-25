"""Autonomous paper-trading loop -- runs as a background task inside the
same process as the Discord bot (see discord_bot.py's setup_hook, gated by
settings.paper_trading_enabled), watching the market during trading hours.

Candidate discovery is deterministic (same reasoning as daily_brief: this
runs unattended, so finding *what's worth looking at* must not depend on
the LLM remembering to check). The actual buy/sell/watch *decision* is
delegated to the same react agent (src/agents/research_agent.py) used for
free-form questions, via the paper_trade_status/buy/sell/watchlist_drop
MCP tools -- Claude decides for itself which data it wants to check
before acting, the same way it would for a user-asked question.

Two position horizons, not just one undifferentiated bucket (per
discussion): a short_term position stays in the tight 30-minute loop so
its exit timing gets watched closely; a long_term position graduates out
of that and only gets reviewed at the broad-scan cadence (40 min) --
"buy and hold, check in periodically" instead of "watch every tick."
The agent itself chooses the horizon when it calls paper_trade_buy.

The watchlist lives in the database (paper_watchlist table), not an
in-memory dict -- see src/memory/paper_trading_store.py's module
docstring for why (the MCP tool subprocess is a different OS process and
can only affect shared state through the DB). Removal is primarily
agent-driven (watchlist_drop, called when the agent decides a candidate
isn't worth tracking anymore), with a long mechanical TTL as a safety net
for candidates the agent simply never acts on.
"""

from __future__ import annotations

import asyncio
import operator as _operator
import time
from datetime import datetime, timedelta, timezone
from datetime import time as dtime

from loguru import logger

from src.agents.daily_brief import _fetch_news
from src.agents.market_agent import _extract_hot_stocks
from src.agents.paper_trading import calc_pnl_pct
from src.agents.research_agent import PAPER_TRADING_SYSTEM, run_research
from src.config import settings
from src.llm import PAPER_TRADING_TOOL_NAMES
from src.memory.paper_trading_store import (
    add_to_watchlist,
    expire_watchlist,
    get_active_conditions,
    get_open_positions,
    get_watchlist,
    log_event,
    mark_condition_triggered,
    touch_watchlist,
)
from src.memory.store import init_storage
from src.tools.chip_data import get_institutional_streak
from src.tools.discord_tools import send_channel_message
from src.tools.market_data import get_quote
from src.tools.paper_trading_actions import buy, sell
from src.tools.stock_data import get_technical_indicators

# Maps a condition's stored operator string to the comparison it performs:
# evaluate(indicator_value, threshold). Kept alongside _check_conditions()
# rather than in paper_trading_actions.py -- that module only validates and
# stores the condition string, this module is the only place that actually
# evaluates it against live data.
_CONDITION_OPERATORS = {
    "lt": _operator.lt,
    "gt": _operator.gt,
    "lte": _operator.le,
    "gte": _operator.ge,
}
_CONDITION_TECHNICAL_FIELDS = (
    "sma_5", "sma_10", "sma_20", "sma_60", "rsi_14", "macd", "macd_signal",
    "macd_hist", "bb_upper", "bb_lower", "ema_12", "kd_k", "kd_d",
    "volume_ratio", "bias_20", "bias_60",
)
_CONDITION_STREAK_FIELDS = ("trust_streak_days", "foreign_streak_days")

_TW_TZ = timezone(timedelta(hours=8))
BROAD_SCAN_INTERVAL = 40 * 60
TIGHT_SCAN_INTERVAL = 30 * 60
WATCHLIST_TTL = 5 * 60 * 60  # safety net only -- see module docstring
TICK_SECONDS = 60
_TRADING_START = dtime(9, 0)
_TRADING_END = dtime(13, 30)

# Daily spend tracking (src.config's paper_trading_daily_budget_usd) -- reset
# whenever the tracked date changes, which naturally happens once a day given
# the loop sleeps overnight between sessions.
_daily_cost_usd = 0.0
_daily_cost_date = None
_budget_notified = False


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


def _reset_cost_if_new_day(now: datetime) -> None:
    """Both _budget_exceeded and _track_cost call this themselves rather
    than relying on being called in a particular order -- a version that
    only reset inside _budget_exceeded() silently accumulated cost against
    a stale day if _track_cost() ever ran first."""
    global _daily_cost_usd, _daily_cost_date, _budget_notified
    today = now.date()
    if _daily_cost_date != today:
        _daily_cost_usd = 0.0
        _daily_cost_date = today
        _budget_notified = False


def _budget_exceeded(now: datetime) -> bool:
    """Reports whether today's cumulative run_research() cost has hit the
    configured cap."""
    _reset_cost_if_new_day(now)
    return _daily_cost_usd >= settings.paper_trading_daily_budget_usd


async def _track_cost(cost_usd: float) -> None:
    """Accumulates spend and fires a one-time Discord notice the moment the
    daily budget is first crossed within a day (not every cycle after)."""
    global _daily_cost_usd, _budget_notified
    _reset_cost_if_new_day(_tw_now())
    _daily_cost_usd += cost_usd
    if _daily_cost_usd >= settings.paper_trading_daily_budget_usd and not _budget_notified:
        _budget_notified = True
        logger.warning(
            f"paper_trading_loop: daily budget exceeded "
            f"(${_daily_cost_usd:.2f} >= ${settings.paper_trading_daily_budget_usd:.2f}), "
            f"pausing decision calls until next session"
        )
        if settings.schedule_report_channel_id:
            await send_channel_message(
                settings.schedule_report_channel_id,
                f"⚠️ 紙上交易今日花費已達上限（${_daily_cost_usd:.2f} / "
                f"${settings.paper_trading_daily_budget_usd:.2f}），暫停決策直到下個交易時段。"
                f"觀察名單仍會繼續更新，但不會再判斷買賣。",
            )
        await log_event(
            "budget_exceeded",
            f"累計花費 ${_daily_cost_usd:.2f} 達上限 "
            f"${settings.paper_trading_daily_budget_usd:.2f}",
        )


async def _check_mechanical_stop_loss() -> None:
    """Hard, non-negotiable safety net -- independent of agent judgment,
    checked every tick against real fetched prices, never calls the LLM so
    it still fires even when the daily budget is exhausted and decision
    calls are paused. Complements (not replaces) the qualitative stop-loss
    rules in the user's knowledge_base notes, which rely on the agent
    correctly reading chart patterns each cycle -- this is what catches a
    bad read or a paused decision loop. Stop-loss only, no take-profit:
    forcing an exit on gains would cut short the notes' own "trailing stop,
    let winners run" logic."""
    positions = await get_open_positions()
    for p in positions:
        threshold = (
            settings.paper_trading_short_term_stop_loss_pct
            if p["horizon"] == "short_term"
            else settings.paper_trading_long_term_stop_loss_pct
        )
        quote = await get_quote(p["symbol"])
        price = quote.get("price")
        if quote.get("error") or not price:
            continue
        pnl = calc_pnl_pct("buy", p["entry_price"], price)
        if pnl is not None and pnl <= -threshold:
            logger.warning(
                f"paper_trading_loop: mechanical stop-loss triggered for "
                f"{p['symbol']} ({pnl}% <= -{threshold}%)"
            )
            await log_event(
                "stop_loss_triggered", f"pnl={pnl}% <= -{threshold}%", symbol=p["symbol"]
            )
            await sell(
                p["symbol"], reason="機械式停損保險觸發（非 agent 判斷）", exit_reason="stop_loss"
            )


async def _check_conditions() -> None:
    """Mechanical, no-LLM-call check for agent-set conditional orders (see
    paper_trading_actions.set_condition). The agent already decided the
    strategy when it called set_condition (which indicator, what threshold,
    buy or sell) -- this just repeats the comparison against real fetched
    data every tick instead of paying for a fresh run_research() call each
    time to re-ask the same question, then executes buy()/sell() directly
    the instant a condition is true.

    A condition fires once: mark_condition_triggered() runs regardless of
    whether the resulting trade actually succeeds, because buy()/sell()
    already reject invalid trades (insufficient cash, position cap,
    duplicate symbol) on their own -- retrying an already-rejected trade
    every tick would just fail the same way forever."""
    conditions = await get_active_conditions()
    if not conditions:
        return

    by_symbol: dict[str, list[dict]] = {}
    for c in conditions:
        by_symbol.setdefault(c["symbol"], []).append(c)

    for symbol, symbol_conditions in by_symbol.items():
        indicator_values: dict[str, float] = {}

        quote = await get_quote(symbol)
        if not quote.get("error") and quote.get("price"):
            indicator_values["close"] = quote["price"]

        technical = await get_technical_indicators(symbol)
        if not technical.get("error"):
            for field in _CONDITION_TECHNICAL_FIELDS:
                value = technical.get(field)
                if value is not None:
                    indicator_values[field] = value

        # Only fetch the institutional streak (several sequential TWSE API
        # calls, cached but still real network work) when a condition on
        # this symbol actually references it -- most conditions won't.
        if any(c["indicator"] in _CONDITION_STREAK_FIELDS for c in symbol_conditions):
            streak = await get_institutional_streak(symbol)
            if not streak.get("error"):
                for field in _CONDITION_STREAK_FIELDS:
                    value = streak.get(field)
                    if value is not None:
                        indicator_values[field] = value

        for c in symbol_conditions:
            value = indicator_values.get(c["indicator"])
            if value is None:
                continue  # couldn't fetch this indicator this tick -- try again next tick
            if not _CONDITION_OPERATORS[c["operator"]](value, c["threshold"]):
                continue

            await mark_condition_triggered(c["id"])
            logger.info(
                f"paper_trading_loop: condition {c['id']} triggered for {symbol} "
                f"({c['indicator']}={value} {c['operator']} {c['threshold']}) -> {c['action']}"
            )
            await log_event(
                "condition_triggered",
                f"id={c['id']} {c['indicator']}={value} {c['operator']} {c['threshold']} "
                f"-> {c['action']}",
                symbol=symbol,
            )
            if c["action"] == "buy":
                result = await buy(
                    symbol, c["reason"] or "條件觸發", c["horizon"], c["allocation_pct"]
                )
            else:
                result = await sell(symbol, c["reason"] or "條件觸發", c["exit_reason"])
            if result.get("error"):
                logger.warning(
                    f"paper_trading_loop: condition {c['id']} trade failed: {result['error']}"
                )


async def _relevant_conditions_block(symbols: set[str]) -> str:
    """Formats active conditions scoped to the given symbols for inclusion
    in a review prompt, so the agent is reminded of a condition it set
    earlier every cycle -- instead of it only resurfacing by the agent
    happening to call paper_trade_status on its own initiative. Lets the
    agent notice a condition no longer makes sense (e.g. new news changed
    the setup) and cancel/replace it, rather than it silently waiting to
    fire on stale reasoning. Returns "" when there's nothing relevant."""
    conditions = await get_active_conditions()
    relevant = [c for c in conditions if c["symbol"] in symbols]
    if not relevant:
        return ""
    lines = [
        f"- id={c['id']}：{c['symbol']} {c['indicator']} {c['operator']} "
        f"{c['threshold']} → {c['action']}"
        for c in relevant
    ]
    return (
        "\n\n你之前設定、還沒觸發的條件單（如果情況已經改變、不再適用，"
        "請呼叫 paper_trade_cancel_condition 取消，需要的話可以用 "
        "paper_trade_set_condition 重新設定）：\n" + "\n".join(lines)
    )


async def _broad_scan() -> None:
    """Every BROAD_SCAN_INTERVAL: (1) discover new watchlist candidates from
    news, (2) sweep the watchlist's mechanical safety-net expiry, (3) give
    long_term positions a periodic check-in -- they're deliberately not
    part of the 30-minute tight scan."""
    logger.info("paper_trading_loop: broad scan (news -> hot stocks)")
    news_articles = await _fetch_news()
    candidates = await _extract_hot_stocks(news_articles)

    open_symbols = {p["symbol"] for p in await get_open_positions()}
    watchlist_symbols = {w["symbol"] for w in await get_watchlist()}
    new_count = 0
    for symbol in candidates:
        if symbol in open_symbols or symbol in watchlist_symbols:
            continue
        await add_to_watchlist(symbol)
        new_count += 1
    logger.info(
        f"paper_trading_loop: broad scan found {len(candidates)} candidates, "
        f"{new_count} new to watchlist"
    )
    await log_event(
        "broad_scan", f"found {len(candidates)} candidates, {new_count} new to watchlist"
    )

    expired = await expire_watchlist(time.time() - WATCHLIST_TTL)
    if expired:
        logger.info(
            f"paper_trading_loop: {expired} expired off watchlist (safety net, never dropped)"
        )

    await _review_long_term_positions()


async def _review_long_term_positions() -> None:
    long_term = await get_open_positions(horizon="long_term")
    if not long_term:
        return

    if _budget_exceeded(_tw_now()):
        logger.info("paper_trading_loop: long-term review skipped, daily budget exceeded")
        return

    logger.info(f"paper_trading_loop: reviewing {len(long_term)} long-term position(s)")
    position_lines = [
        f"- {p['symbol']}（進場 {p['entry_price']}，{p['entry_date']}，長期持有）"
        for p in long_term
    ]
    condition_block = await _relevant_conditions_block({p["symbol"] for p in long_term})
    prompt = (
        "這是長期持有部位的例行檢視（較低頻率，每次廣掃才會問一次，不是每 30 分鐘）。"
        "請檢查以下長期部位，只有在有明確理由時才考慮賣出，否則維持長期持有的初衷，"
        "不需要因為短線波動就出場。務必實際查證真實數據後再決定。\n\n"
        "目前長期持有部位：\n" + "\n".join(position_lines) + condition_block
    )
    try:
        result = await run_research(
            prompt,
            [],
            tool_names=PAPER_TRADING_TOOL_NAMES,
            system_prompt=PAPER_TRADING_SYSTEM,
        )
        await _track_cost(result.get("cost_usd", 0.0))
        conclusion = result.get("conclusion", "")
        logger.info(f"paper_trading_loop: long-term review conclusion — {conclusion}")
        await log_event("long_term_review", conclusion)
    except Exception as exc:
        logger.warning(f"paper_trading_loop: long-term review failed: {exc}")


async def _tight_scan() -> None:
    """Every TIGHT_SCAN_INTERVAL: watchlist candidates + short_term
    positions only -- long_term positions are handled by the broad scan
    instead, at a much lower frequency."""
    now_ts = _tw_now().timestamp()
    watchlist = await get_watchlist()
    short_term_positions = await get_open_positions(horizon="short_term")

    due_watchlist = [
        w["symbol"] for w in watchlist if now_ts - w["last_checked"] >= TIGHT_SCAN_INTERVAL
    ]
    if not due_watchlist and not short_term_positions:
        return

    if _budget_exceeded(_tw_now()):
        logger.info("paper_trading_loop: tight scan skipped, daily budget exceeded")
        return

    logger.info(
        f"paper_trading_loop: tight scan — {len(due_watchlist)} watchlist due, "
        f"{len(short_term_positions)} short-term positions"
    )

    watchlist_lines = [f"- {s}" for s in due_watchlist] or ["（無）"]
    position_lines = [
        f"- {p['symbol']}（進場 {p['entry_price']}，{p['entry_date']}）"
        for p in short_term_positions
    ] or ["（無）"]
    condition_block = await _relevant_conditions_block(
        set(due_watchlist) | {p["symbol"] for p in short_term_positions}
    )

    prompt = (
        "現在是台股交易時段，這是紙上交易（模擬帳戶）短線操作的例行檢查。請檢查以下清單，"
        "自行判斷是否要對其中任何股票採取行動（買進、賣出、或都不動作）。"
        "務必實際呼叫工具查證真實數據後再決定，不要只憑下方名單文字判斷。"
        "若判斷某支股票不用再追蹤了，請呼叫 watchlist_drop 移除。\n\n"
        "觀察名單（尚未持有，值得留意的候選股）：\n" + "\n".join(watchlist_lines) + "\n\n"
        "目前短線持有中部位：\n" + "\n".join(position_lines) + condition_block
    )

    try:
        # Only this call site (and _review_long_term_positions) opts into the
        # Trading research tools include sector/theme and all required market
        # checks, but deliberately exclude unrelated Discord/Gmail tools.
        result = await run_research(
            prompt,
            [],
            tool_names=PAPER_TRADING_TOOL_NAMES,
            system_prompt=PAPER_TRADING_SYSTEM,
        )
        await _track_cost(result.get("cost_usd", 0.0))
        conclusion = result.get("conclusion", "")
        logger.info(f"paper_trading_loop: cycle conclusion — {conclusion}")
        await log_event(
            "tight_scan",
            f"{len(due_watchlist)} watchlist due, {len(short_term_positions)} "
            f"short-term positions — {conclusion}",
        )
    except Exception as exc:
        logger.warning(f"paper_trading_loop: research call failed: {exc}")
        return

    # Only touch symbols still actually on the watchlist -- a buy or a
    # watchlist_drop during this cycle already removed them from the DB.
    remaining = {w["symbol"] for w in await get_watchlist()}
    still_due = [s for s in due_watchlist if s in remaining]
    await touch_watchlist(still_due, now_ts)


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
            # Every tick, not gated by an interval -- cheap (price/technical
            # fetch only, no LLM call) and must not wait on the same cadence
            # as the agentic scans they stand in for or guard against.
            await _check_mechanical_stop_loss()
            await _check_conditions()
            if now_ts - last_broad >= BROAD_SCAN_INTERVAL:
                await _broad_scan()
                last_broad = now_ts
            if now_ts - last_tight >= TIGHT_SCAN_INTERVAL:
                await _tight_scan()
                last_tight = now_ts
        except Exception as exc:
            logger.error(f"paper_trading_loop: cycle error: {exc}", exc_info=True)

        await asyncio.sleep(TICK_SECONDS)
