"""Paper-trading evaluation: checks the position lifecycle recorded by
src/agents/paper_trading_loop.py against real prices, with no real or
third-party trading account involved.
"""

from __future__ import annotations

import asyncio

from src.memory.paper_trading_store import get_all_positions
from src.tools.stock_data import get_stock_price


def calc_pnl_pct(direction: str, entry_price: float, current_price: float) -> float | None:
    """% return if this call had been followed. buy profits when price
    rises; sell (short-style) profits when price falls; hold has no
    position so there is no P&L. entry_price <= 0 is invalid (division by
    zero) and returns None rather than raising."""
    if direction == "hold" or not entry_price:
        return None
    change = (current_price - entry_price) / entry_price * 100
    return change if direction == "buy" else -change


async def evaluate_paper_trades() -> dict:
    """Every open position gets a floating P&L against a freshly fetched
    current price (one call per unique symbol, not per position); every
    closed position gets its realized P&L from entry_price vs. exit_price.
    Aggregate win rate / average return are computed over closed positions
    only -- an open position hasn't proven anything yet."""
    positions = await get_all_positions()
    if not positions:
        return {
            "positions": [],
            "open_count": 0,
            "closed_count": 0,
            "win_rate": None,
            "avg_return_pct": None,
        }

    open_positions = [p for p in positions if p["status"] == "open"]
    closed_positions = [p for p in positions if p["status"] == "closed"]

    symbols = list({p["symbol"] for p in open_positions})
    prices = await asyncio.gather(*[get_stock_price(s) for s in symbols], return_exceptions=True)
    current_price_map = {}
    for symbol, result in zip(symbols, prices):
        if isinstance(result, Exception) or result.get("error"):
            continue
        current_price_map[symbol] = result.get("last_price")

    scored = []
    for p in positions:
        if p["status"] == "open":
            current_price = current_price_map.get(p["symbol"])
            pnl = (
                calc_pnl_pct("buy", p["entry_price"], current_price)
                if current_price is not None
                else None
            )
            scored.append({**p, "current_price": current_price, "pnl_pct": pnl})
        else:
            pnl = calc_pnl_pct("buy", p["entry_price"], p["exit_price"])
            scored.append({**p, "current_price": p["exit_price"], "pnl_pct": pnl})

    closed_pnls = [
        s["pnl_pct"] for s in scored if s["status"] == "closed" and s["pnl_pct"] is not None
    ]
    wins = sum(1 for pnl in closed_pnls if pnl > 0)
    total_closed = len(closed_pnls)

    return {
        "positions": scored,
        "open_count": len(open_positions),
        "closed_count": len(closed_positions),
        "win_rate": round(wins / total_closed * 100, 1) if total_closed else None,
        "avg_return_pct": round(sum(closed_pnls) / total_closed, 2) if total_closed else None,
    }
