"""Paper-trading execution actions -- the real logic behind the
paper_trade_status/buy/sell MCP tools (registered in src/mcp_server.py,
which only formats these dicts into text, same convention as every other
tool in this project).

Separate from src/agents/paper_trading.py (P&L math + evaluate_paper_trades,
shared with the /performance command) and src/memory/paper_trading_store.py
(the DB layer) -- this module is specifically the "do a trade" action.
"""

from __future__ import annotations

from src.agents.paper_trading import calc_pnl_pct, evaluate_paper_trades
from src.config import settings
from src.memory.paper_trading_store import (
    close_position,
    get_open_positions,
    open_position,
    remove_from_watchlist,
)
from src.tools.discord_tools import send_channel_message
from src.tools.stock_data import get_stock_price

_VALID_EXIT_REASONS = {"take_profit", "stop_loss", "llm_signal"}
_VALID_HORIZONS = {"short_term", "long_term"}


async def get_status() -> dict:
    """Same shape as evaluate_paper_trades(): {"positions": [...], ...}."""
    return await evaluate_paper_trades()


async def buy(symbol: str, reason: str, horizon: str = "short_term") -> dict:
    """Opens a position at the real current price. Returns {"error": str}
    on failure, or {"success": True, "symbol", "price", "position_id"}.
    Also removes the symbol from the watchlist if it was on one -- once
    bought, it is tracked as a position, not a candidate."""
    if horizon not in _VALID_HORIZONS:
        horizon = "short_term"

    existing = [p for p in await get_open_positions() if p["symbol"] == symbol]
    if existing:
        return {"error": f"{symbol} 已經有持有中的部位（id={existing[0]['id']}），不可重複買進"}

    max_positions = (
        settings.paper_trading_max_short_term_positions
        if horizon == "short_term"
        else settings.paper_trading_max_long_term_positions
    )
    same_horizon_count = len(await get_open_positions(horizon=horizon))
    if same_horizon_count >= max_positions:
        horizon_label = "短線操作" if horizon == "short_term" else "長期持有"
        return {
            "error": (
                f"{horizon_label}部位已達上限（{same_horizon_count}/{max_positions}），"
                f"須先賣出既有部位才能買進 {symbol}"
            )
        }

    price_data = await get_stock_price(symbol)
    price = price_data.get("last_price")
    if price_data.get("error") or not price:
        return {
            "error": f"{symbol} 無法取得即時股價，交易取消：{price_data.get('error', '無資料')}"
        }

    position_id = await open_position(symbol, price, reason, horizon)
    await remove_from_watchlist(symbol)
    horizon_label = "短線操作" if horizon == "short_term" else "長期持有"
    await _notify(f"📈 紙上交易買進：{symbol} @ {price}（{horizon_label}）\n理由：{reason}")
    return {"success": True, "symbol": symbol, "price": price, "position_id": position_id}


async def sell(symbol: str, reason: str, exit_reason: str) -> dict:
    """Closes an existing position at the real current price. Returns
    {"error": str} on failure, or {"success": True, "symbol", "price",
    "exit_reason", "pnl_pct"}."""
    existing = [p for p in await get_open_positions() if p["symbol"] == symbol]
    if not existing:
        return {"error": f"{symbol} 目前沒有持有中的部位可以賣出"}
    if exit_reason not in _VALID_EXIT_REASONS:
        exit_reason = "llm_signal"

    price_data = await get_stock_price(symbol)
    price = price_data.get("last_price")
    if price_data.get("error") or not price:
        return {
            "error": f"{symbol} 無法取得即時股價，交易取消：{price_data.get('error', '無資料')}"
        }

    position = existing[0]
    await close_position(position["id"], price, exit_reason)
    pnl = calc_pnl_pct("buy", position["entry_price"], price)
    await _notify(
        f"📉 紙上交易賣出：{symbol} @ {price}（{exit_reason}，損益 {pnl}%）\n理由：{reason}"
    )
    return {
        "success": True, "symbol": symbol, "price": price,
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


async def _notify(message: str) -> None:
    from loguru import logger

    from src.config import settings

    if not settings.schedule_report_channel_id:
        return
    result = await send_channel_message(settings.schedule_report_channel_id, message)
    if result.get("error"):
        logger.warning(f"paper_trading_actions: notify failed: {result['error']}")
