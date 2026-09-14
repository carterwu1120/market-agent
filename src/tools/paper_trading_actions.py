"""Paper-trading execution actions -- the real logic behind the
paper_trade_status/buy/sell MCP tools (registered in src/mcp_server.py,
which only formats these dicts into text, same convention as every other
tool in this project).

Separate from src/agents/paper_trading.py (P&L math + evaluate_paper_trades,
shared with the /performance command) and src/memory/paper_trading_store.py
(the DB layer) -- this module is specifically the business rules (horizon/
allocation validation, position caps, cash checks) that apply no matter how
a trade actually gets executed. Execution itself goes through the generic
Broker seam (src/tools/broker.py), currently backed by PaperBroker
(src/tools/paper_broker.py) -- see docs/adr/0002-execution-backend-seam.md
for why that split exists.
"""

from __future__ import annotations

import asyncio

from src.agents.paper_trading import (
    calc_allocation,
    calc_pnl_pct,
    evaluate_paper_trades,
    simulate_portfolio_equity,
)
from src.config import settings
from src.memory.paper_trading_store import (
    add_condition,
    cancel_condition,
    get_active_conditions,
    log_event,
    remove_from_watchlist,
)
from src.tools.broker import Broker
from src.tools.discord_tools import send_channel_message
from src.tools.market_data import get_quote
from src.tools.paper_broker import PaperBroker

# Typed against the generic Broker seam, not PaperBroker directly -- the
# business-rule logic below (buy()/sell()) only ever depends on that shape.
_broker: Broker = PaperBroker()

_VALID_EXIT_REASONS = {"take_profit", "stop_loss", "llm_signal"}
_VALID_HORIZONS = {"short_term", "long_term"}

# Indicators a conditional order can reference -- "close" comes from
# MarketDataProvider.get_quote() (currently delayed Yahoo price); trust_streak_days/
# foreign_streak_days come from get_institutional_streak() (chip_data.py);
# everything else comes from get_technical_indicators() (daily-bar-derived,
# same values /stock's technical_analysis tool already surfaces). Kept as a
# whitelist rather than accepting any string so a mistyped indicator fails
# loudly at set_condition time instead of silently never triggering at
# check time. sma_5/sma_10/kd_k/kd_d/volume_ratio/*_streak_days exist
# specifically because the user's own knowledge_base strategy notes lean on
# them (五日/十日均線, KD黃金/死亡交叉, 帶量/爆量, 投信連續買超).
_VALID_CONDITION_INDICATORS = {
    "close", "sma_5", "sma_10", "sma_20", "sma_60", "rsi_14",
    "macd", "macd_signal", "macd_hist", "bb_upper", "bb_lower", "ema_12",
    "kd_k", "kd_d", "volume_ratio", "bias_20", "bias_60",
    "trust_streak_days", "foreign_streak_days",
}
_VALID_CONDITION_OPERATORS = {"lt", "gt", "lte", "gte"}
_VALID_CONDITION_ACTIONS = {"buy", "sell"}

# buy()/sell() each do several check-then-act reads (duplicate-symbol,
# position cap, cash available) before the eventual DB write, with await
# points in between -- not atomic on their own. This serializes those calls
# within this process/event loop so two concurrent tool calls (e.g. Claude
# dispatching two paper_trade_buy calls in the same turn) can't both pass
# the same check and double-buy a symbol or over-commit simulated cash.
# Does not protect across separate OS processes -- not needed here since
# paper_trading_loop.py's run() awaits each scan to completion sequentially
# rather than running them concurrently.
_trade_lock = asyncio.Lock()

_OPERATOR_ALIASES = {
    "<": "lt", ">": "gt", "<=": "lte", ">=": "gte",
    "less_than": "lt", "greater_than": "gt",
    "less_than_or_equal": "lte", "greater_than_or_equal": "gte",
}


async def get_status() -> dict:
    """evaluate_paper_trades()'s shape plus an "equity" key (see
    simulate_portfolio_equity) and a "conditions" key (currently active
    conditional orders, see set_condition) so the agent can see how much
    simulated cash it has and what's already pending before deciding
    whether to also set a new condition."""
    result = await evaluate_paper_trades()
    result["equity"] = simulate_portfolio_equity(result["positions"])
    result["conditions"] = await get_active_conditions()
    return result


async def buy(
    symbol: str, reason: str, horizon: str = "short_term", allocation_pct: float = 10.0
) -> dict:
    """Opens a position at the configured provider's current quote. allocation_pct (% of the
    fixed starting capital, not current equity) is the agent's own call on
    conviction/sizing, clamped into [min, max] by calc_allocation() so one
    overconfident call can't all-in a single symbol. Returns {"error": str}
    on failure, or {"success": True, "symbol", "price", "shares",
    "allocation_amount", "position_id"}. Also removes the symbol from the
    watchlist if it was on one -- once bought, it is tracked as a position,
    not a candidate."""
    if horizon not in _VALID_HORIZONS:
        return {"error": "horizon 必須明確是 short_term 或 long_term"}

    async with _trade_lock:
        existing = [p for p in await _broker.get_positions() if p["symbol"] == symbol]
        if existing:
            return {
                "error": f"{symbol} 已經有持有中的部位（id={existing[0]['id']}），不可重複買進"
            }

        max_positions = (
            settings.paper_trading_max_short_term_positions
            if horizon == "short_term"
            else settings.paper_trading_max_long_term_positions
        )
        same_horizon_count = len(await _broker.get_positions(horizon=horizon))
        if same_horizon_count >= max_positions:
            horizon_label = "短線操作" if horizon == "short_term" else "長期持有"
            return {
                "error": (
                    f"{horizon_label}部位已達上限（{same_horizon_count}/{max_positions}），"
                    f"須先賣出既有部位才能買進 {symbol}"
                )
            }

        quote = await get_quote(symbol)
        price = quote.get("price")
        if quote.get("error") or not price:
            return {
                "error": f"{symbol} 無法取得行情，交易取消：{quote.get('error', '無資料')}"
            }

        shares, allocation_amount = calc_allocation(allocation_pct, price)
        if shares < 1:
            return {"error": f"{symbol} 股價 {price} 過高，分配額度買不到 1 股，交易取消"}

        available_cash = await _broker.get_cash()
        if allocation_amount > available_cash:
            return {
                "error": (
                    f"模擬現金不足（需要 {allocation_amount:.0f}，可用 {available_cash:.0f}），"
                    f"須先賣出既有部位才能買進 {symbol}"
                )
            }

        fill = await _broker.execute_buy(
            symbol, price, shares, allocation_amount, reason, horizon
        )
        if fill is None:
            return {"error": f"{symbol} 交易執行失敗，請稍後再試"}

    # Everything downstream uses fill["fill_price"], not the reference price
    # fetched above -- for PaperBroker they're always equal, but a real
    # backend may fill at a different price (slippage). See broker.py.
    fill_price = fill["fill_price"]
    horizon_label = "短線操作" if horizon == "short_term" else "長期持有"
    await _notify(
        f"📈 紙上交易買進：{symbol} @ {fill_price} x {shares} 股（約 {allocation_amount:.0f} 元，"
        f"{horizon_label}）\n理由：{reason}"
    )
    await log_event(
        "buy",
        f"{fill_price} x {shares} 股（約 {allocation_amount:.0f} 元，"
        f"{horizon_label}）理由：{reason}",
        symbol=symbol,
    )
    return {
        "success": True, "symbol": symbol, "price": fill_price, "shares": shares,
        "allocation_amount": allocation_amount, "position_id": fill["position_id"],
    }


async def sell(symbol: str, reason: str, exit_reason: str) -> dict:
    """Closes an existing position at the configured provider's current quote. Returns
    {"error": str} on failure, or {"success": True, "symbol", "price",
    "exit_reason", "pnl_pct"}."""
    async with _trade_lock:
        existing = [p for p in await _broker.get_positions() if p["symbol"] == symbol]
        if not existing:
            return {"error": f"{symbol} 目前沒有持有中的部位可以賣出"}
        if exit_reason not in _VALID_EXIT_REASONS:
            exit_reason = "llm_signal"

        quote = await get_quote(symbol)
        price = quote.get("price")
        if quote.get("error") or not price:
            return {
                "error": f"{symbol} 無法取得行情，交易取消：{quote.get('error', '無資料')}"
            }

        position = existing[0]
        fill = await _broker.execute_sell(position["id"], price, exit_reason)

    # fill["fill_price"], not the reference price fetched above -- see
    # broker.py; PaperBroker's are always equal, a real backend's may not be.
    fill_price = fill["fill_price"]
    pnl = calc_pnl_pct("buy", position["entry_price"], fill_price)
    await _notify(
        f"📉 紙上交易賣出：{symbol} @ {fill_price}（{exit_reason}，損益 {pnl}%）\n理由：{reason}"
    )
    await log_event(
        "sell", f"{fill_price}（{exit_reason}，損益 {pnl}%）理由：{reason}", symbol=symbol
    )
    return {
        "success": True, "symbol": symbol, "price": fill_price,
        "exit_reason": exit_reason, "pnl_pct": pnl,
    }


async def drop_watchlist(symbol: str, reason: str) -> dict:
    """Removes a candidate from the watchlist -- the agent's own judgment
    call that it is done tracking this symbol (decided not to trade it, or
    already finished trading it), replacing a mechanical time-based expiry
    as the primary removal path. Returns {"error": str} if the symbol
    was not on the watchlist, else {"success": True, "symbol"}."""
    removed = await remove_from_watchlist(symbol)
    if not removed:
        return {"error": f"{symbol} 不在觀察名單上"}
    return {"success": True, "symbol": symbol}


async def set_condition(
    symbol: str,
    indicator: str,
    operator: str,
    threshold: float,
    action: str,
    reason: str = "",
    horizon: str = "short_term",
    allocation_pct: float = 10.0,
    exit_reason: str = "llm_signal",
) -> dict:
    """Records a conditional order: paper_trading_loop.py's mechanical
    per-tick check (no LLM call) evaluates indicator/operator/threshold
    against real fetched data and executes buy()/sell() directly the
    moment it's true -- the agent already decided the strategy (which
    indicator, what threshold, buy or sell) here; the mechanical part is
    only the repeated checking, same "agent decides parameters, code
    enforces them" shape as horizon/allocation_pct/the stop-loss safety net.
    Returns {"error": str} on an invalid indicator/operator/action, else
    {"success": True, "condition_id"}."""
    operator = _OPERATOR_ALIASES.get(operator.strip().lower(), operator.strip().lower())
    if indicator not in _VALID_CONDITION_INDICATORS:
        return {"error": f"indicator 必須是以下之一：{sorted(_VALID_CONDITION_INDICATORS)}"}
    if operator not in _VALID_CONDITION_OPERATORS:
        return {"error": f"operator 必須是以下之一：{sorted(_VALID_CONDITION_OPERATORS)}"}
    if action not in _VALID_CONDITION_ACTIONS:
        return {"error": f"action 必須是以下之一：{sorted(_VALID_CONDITION_ACTIONS)}"}
    if action == "buy" and horizon not in _VALID_HORIZONS:
        return {"error": "買入條件的 horizon 必須明確是 short_term 或 long_term"}
    if action == "sell" and exit_reason not in _VALID_EXIT_REASONS:
        exit_reason = "llm_signal"

    condition_id = await add_condition(
        symbol, indicator, operator, threshold, action, reason, horizon,
        allocation_pct, exit_reason,
    )
    if condition_id is None:
        return {"error": "條件寫入失敗，請稍後再試"}
    await log_event(
        "condition_set",
        f"id={condition_id} {indicator} {operator} {threshold} -> {action}",
        symbol=symbol,
    )
    return {"success": True, "condition_id": condition_id}


async def cancel_watch_condition(condition_id: int) -> dict:
    """Cancels a still-active conditional order before it triggers --
    the agent's own judgment that the setup is no longer valid. Returns
    {"error": str} if no active condition with this id exists, else
    {"success": True, "condition_id"}."""
    cancelled = await cancel_condition(condition_id)
    if not cancelled:
        return {"error": f"找不到 id={condition_id} 的有效條件（可能已觸發或已取消）"}
    await log_event("condition_cancelled", f"id={condition_id}")
    return {"success": True, "condition_id": condition_id}


async def _notify(message: str) -> None:
    from loguru import logger

    from src.config import settings

    if not settings.schedule_report_channel_id:
        return
    result = await send_channel_message(settings.schedule_report_channel_id, message)
    if result.get("error"):
        logger.warning(f"paper_trading_actions: notify failed: {result['error']}")
