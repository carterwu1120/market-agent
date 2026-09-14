"""Autonomous paper-trading loop -- runs as a background task inside the
same process as the Discord bot (see discord_bot.py's setup_hook, gated by
settings.paper_trading_enabled), watching the market during trading hours.

Candidate discovery and market-data collection are deterministic. Background
decisions use a bounded two-symbol packet and one structured LLM call; the
open-ended ReAct/MCP loop is reserved for user-initiated Discord research.

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
from src.agents.paper_trading_decision import run_paper_decision
from src.config import settings
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
from src.tools.company_moat import get_company_moat_evidence
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
DECISION_BATCH_SIZE = 2
TICK_SECONDS = 60
_TRADING_START = dtime(9, 0)
_TRADING_END = dtime(13, 30)

# Daily spend tracking (src.config's paper_trading_daily_budget_usd) -- reset
# whenever the tracked date changes, which naturally happens once a day given
# the loop sleeps overnight between sessions.
_daily_cost_usd = 0.0
_daily_cost_date = None
_budget_notified = False
_daily_llm_calls = 0
_daily_timeouts = 0
_position_last_checked: dict[str, float] = {}


def _tw_now() -> datetime:
    return datetime.now(_TW_TZ)


def _is_trading_hours(now: datetime) -> bool:
    return now.weekday() < 5 and _TRADING_START <= now.time() <= _TRADING_END


def _select_due_watchlist(
    watchlist: list[dict], now_ts: float, limit: int = DECISION_BATCH_SIZE
) -> tuple[list[str], int]:
    """Return one fair research batch and the number left for later cycles.

    Entries checked least recently go first; ``first_seen`` breaks ties so a
    growing watchlist cannot starve older candidates indefinitely.
    """
    due = [
        item
        for item in watchlist
        if now_ts - item["last_checked"] >= TIGHT_SCAN_INTERVAL
    ]
    due.sort(key=lambda item: (item["last_checked"], item["first_seen"]))
    selected = due[:limit]
    return [item["symbol"] for item in selected], max(0, len(due) - len(selected))


def _select_positions(positions: list[dict], limit: int) -> list[dict]:
    """Fair in-memory rotation for open positions; oldest review goes first."""
    return sorted(
        positions,
        key=lambda item: (_position_last_checked.get(item["symbol"], 0.0), item["created_at"]),
    )[:limit]


def _build_targets(
    watchlist: list[dict], positions: list[dict], now_ts: float
) -> tuple[list[dict], int]:
    """Use one slot per role first, then fill any spare slot from the other role."""
    position_batch = _select_positions(positions, 1) if positions else []
    watch_limit = DECISION_BATCH_SIZE - len(position_batch)
    due_watchlist, queued = _select_due_watchlist(watchlist, now_ts, watch_limit)
    if len(due_watchlist) < watch_limit and positions:
        position_batch = _select_positions(positions, DECISION_BATCH_SIZE - len(due_watchlist))

    watchlist_by_symbol = {item["symbol"]: item for item in watchlist}
    targets = [
        {
            "symbol": symbol,
            "role": "watchlist",
            "watchlist": watchlist_by_symbol[symbol],
        }
        for symbol in due_watchlist
    ]
    targets.extend(
        {
            "symbol": position["symbol"],
            "role": "position",
            "position": position,
        }
        for position in position_batch
    )
    return targets, queued


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
    global _daily_llm_calls, _daily_timeouts
    today = now.date()
    if _daily_cost_date != today:
        _daily_cost_usd = 0.0
        _daily_cost_date = today
        _budget_notified = False
        _daily_llm_calls = 0
        _daily_timeouts = 0


def _budget_exceeded(now: datetime) -> bool:
    """Reports whether today's cumulative run_research() cost has hit the
    configured cap."""
    _reset_cost_if_new_day(now)
    return (
        _daily_cost_usd >= settings.paper_trading_daily_budget_usd
        or _daily_llm_calls >= settings.paper_trading_max_llm_calls_per_day
        or _daily_timeouts >= settings.paper_trading_max_timeouts_per_day
    )


def _decision_limit_reason(now: datetime) -> str:
    _reset_cost_if_new_day(now)
    if _daily_timeouts >= settings.paper_trading_max_timeouts_per_day:
        return f"timeouts {_daily_timeouts}/{settings.paper_trading_max_timeouts_per_day}"
    if _daily_llm_calls >= settings.paper_trading_max_llm_calls_per_day:
        return f"calls {_daily_llm_calls}/{settings.paper_trading_max_llm_calls_per_day}"
    if _daily_cost_usd >= settings.paper_trading_daily_budget_usd:
        return f"cost ${_daily_cost_usd:.2f}/${settings.paper_trading_daily_budget_usd:.2f}"
    return ""


async def _notify_decision_limit(reason: str) -> None:
    global _budget_notified
    if not reason or _budget_notified:
        return
    _budget_notified = True
    message = f"紙上交易決策今日已暫停（{reason}）；機械停損與條件單仍會執行。"
    logger.warning("paper_trading_loop: {}", message)
    if settings.schedule_report_channel_id:
        await send_channel_message(settings.schedule_report_channel_id, f"⚠️ {message}")
    await log_event("budget_exceeded", message)


def _record_llm_call() -> None:
    global _daily_llm_calls
    _reset_cost_if_new_day(_tw_now())
    _daily_llm_calls += 1


def _record_llm_failure(error: Exception) -> None:
    global _daily_timeouts
    _reset_cost_if_new_day(_tw_now())
    if "timeout" in str(error).lower() or "timed out" in str(error).lower():
        _daily_timeouts += 1


def _decision_summary(result: dict) -> str:
    parts = []
    for item in result.get("results", []):
        suffix = f" error={item['error']}" if item.get("error") else ""
        parts.append(f"{item.get('symbol')}:{item.get('action')}{suffix}")
    return ", ".join(parts) or "no valid decisions"


def _attach_conditions(targets: list[dict], conditions: list[dict]) -> None:
    by_symbol: dict[str, list[dict]] = {}
    for condition in conditions:
        by_symbol.setdefault(condition["symbol"], []).append(condition)
    for target in targets:
        target["conditions"] = by_symbol.get(target["symbol"], [])


def _defer_failed_targets(targets: list[dict], now_ts: float) -> tuple[list[str], list[str]]:
    cooldown = settings.paper_trading_failure_cooldown_seconds
    retry_marker = now_ts + max(0, cooldown - TIGHT_SCAN_INTERVAL)
    watch_symbols = [t["symbol"] for t in targets if t["role"] == "watchlist"]
    position_symbols = [t["symbol"] for t in targets if t["role"] == "position"]
    for symbol in position_symbols:
        _position_last_checked[symbol] = retry_marker
    return watch_symbols, position_symbols


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
    new_symbols: list[str] = []
    for symbol in candidates:
        if symbol in open_symbols or symbol in watchlist_symbols:
            continue
        await add_to_watchlist(symbol)
        new_symbols.append(symbol)
        new_count += 1
    logger.info(
        f"paper_trading_loop: broad scan found {len(candidates)} candidates, "
        f"{new_count} new to watchlist"
    )
    await log_event(
        "broad_scan", f"found {len(candidates)} candidates, {new_count} new to watchlist"
    )

    # Investigate a newly discovered candidate once, not on every tight scan.
    # The helper has a seven-day SQLite cache and this batch is deliberately bounded.
    if new_symbols:
        moat_results = await asyncio.gather(
            *(get_company_moat_evidence(symbol) for symbol in new_symbols[:2]),
            return_exceptions=True,
        )
        failures = sum(isinstance(result, Exception) for result in moat_results)
        logger.info(
            f"paper_trading_loop: moat evidence warmed for "
            f"{len(moat_results) - failures}/{len(moat_results)} new candidates"
        )

    expired = await expire_watchlist(time.time() - WATCHLIST_TTL)
    if expired:
        logger.info(
            f"paper_trading_loop: {expired} expired off watchlist (safety net, never dropped)"
        )

    await _review_long_term_positions()


async def _review_long_term_positions() -> None:
    now_ts = _tw_now().timestamp()
    long_term = await get_open_positions(horizon="long_term")
    due_positions = [
        item for item in long_term
        if now_ts - _position_last_checked.get(item["symbol"], 0.0)
        >= settings.paper_trading_long_term_review_seconds
    ]
    long_watchlist = [
        item for item in await get_watchlist()
        if item.get("strategy_horizon") == "long_term"
        and now_ts - item["last_checked"] >= settings.paper_trading_long_term_review_seconds
    ]
    if not due_positions and not long_watchlist:
        return

    limit_reason = _decision_limit_reason(_tw_now())
    if limit_reason:
        await _notify_decision_limit(limit_reason)
        logger.info("paper_trading_loop: long-term review skipped, decision limit reached")
        return

    selected = _select_positions(due_positions, 1 if long_watchlist else DECISION_BATCH_SIZE)
    targets: list[dict] = [
        {"symbol": position["symbol"], "role": "position", "position": position}
        for position in selected
    ]
    remaining = DECISION_BATCH_SIZE - len(targets)
    targets.extend(
        {
            "symbol": item["symbol"],
            "role": "watchlist",
            "watchlist": item,
        }
        for item in sorted(long_watchlist, key=lambda item: item["last_checked"])[:remaining]
    )
    _attach_conditions(targets, await get_active_conditions())
    # Refresh only for this bounded weekly batch. Decision Packet collection
    # immediately reuses the SQLite cache and performs no duplicate search.
    await asyncio.gather(
        *(get_company_moat_evidence(target["symbol"]) for target in targets),
        return_exceptions=True,
    )
    logger.info(
        "paper_trading_loop: reviewing {} target(s): {} long-term position(s), "
        "{} long-term watchlist candidate(s)",
        len(targets),
        len(long_term),
        len(long_watchlist),
    )
    try:
        _record_llm_call()
        result = await run_paper_decision(targets)
        await _track_cost(result.get("cost_usd", 0.0))
        conclusion = _decision_summary(result)
        for target in targets:
            if target["role"] == "position":
                _position_last_checked[target["symbol"]] = now_ts
        remaining_watchlist = {item["symbol"] for item in await get_watchlist()}
        await touch_watchlist(
            [
                target["symbol"] for target in targets
                if target["role"] == "watchlist" and target["symbol"] in remaining_watchlist
            ],
            now_ts,
        )
        logger.info(f"paper_trading_loop: long-term review conclusion — {conclusion}")
        await log_event("long_term_review", conclusion)
    except Exception as exc:
        _record_llm_failure(exc)
        _defer_failed_targets(targets, _tw_now().timestamp())
        logger.warning(f"paper_trading_loop: long-term review failed: {exc}")


async def _tight_scan() -> None:
    """Every TIGHT_SCAN_INTERVAL: watchlist candidates + short_term
    positions only -- long_term positions are handled by the broad scan
    instead, at a much lower frequency."""
    now_ts = _tw_now().timestamp()
    watchlist = [
        item for item in await get_watchlist()
        if item.get("strategy_horizon") != "long_term"
    ]
    short_term_positions = await get_open_positions(horizon="short_term")

    targets, queued_watchlist_count = _build_targets(watchlist, short_term_positions, now_ts)
    if not targets:
        return

    limit_reason = _decision_limit_reason(_tw_now())
    if limit_reason:
        await _notify_decision_limit(limit_reason)
        logger.info("paper_trading_loop: tight scan skipped, decision limit reached")
        return

    logger.info(
        "paper_trading_loop: tight scan — {} target(s), {} watchlist queued, "
        "{} short-term positions total",
        len(targets),
        queued_watchlist_count,
        len(short_term_positions),
    )
    _attach_conditions(targets, await get_active_conditions())
    # Every candidate eventually receives the long-term evidence classifier,
    # even when it was outside the broad scan's two-symbol prewarm cap. Cache
    # hits are local SQLite reads; only missing evidence performs web search.
    candidate_targets = [target for target in targets if target["role"] == "watchlist"]
    await asyncio.gather(
        *(get_company_moat_evidence(target["symbol"]) for target in candidate_targets),
        return_exceptions=True,
    )

    try:
        _record_llm_call()
        result = await run_paper_decision(targets)
        await _track_cost(result.get("cost_usd", 0.0))
        conclusion = _decision_summary(result)
        logger.info(f"paper_trading_loop: cycle conclusion — {conclusion}")
        await log_event(
            "tight_scan",
            f"{len(targets)} targets checked, {queued_watchlist_count} watchlist queued — "
            f"{conclusion}",
        )
    except Exception as exc:
        _record_llm_failure(exc)
        failed_watchlist, _ = _defer_failed_targets(targets, now_ts)
        if failed_watchlist:
            await touch_watchlist(
                failed_watchlist,
                now_ts + max(
                    0,
                    settings.paper_trading_failure_cooldown_seconds - TIGHT_SCAN_INTERVAL,
                ),
            )
        logger.warning(f"paper_trading_loop: research call failed: {exc}")
        detail = (
            f"symbols={','.join(target['symbol'] for target in targets)}; "
            f"queued={queued_watchlist_count}; "
            f"error={str(exc)[:500]}"
        )
        await log_event("research_failed", detail)
        return

    for target in targets:
        if target["role"] == "position":
            _position_last_checked[target["symbol"]] = now_ts
    remaining = {w["symbol"] for w in await get_watchlist()}
    still_due = [
        target["symbol"]
        for target in targets
        if target["role"] == "watchlist" and target["symbol"] in remaining
    ]
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
