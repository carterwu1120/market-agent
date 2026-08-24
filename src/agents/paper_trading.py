"""Paper-trading evaluation: checks the position lifecycle recorded by
src/agents/paper_trading_loop.py against real prices, with no real or
third-party trading account involved.
"""

from __future__ import annotations

import asyncio

from src.config import settings
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


def calc_allocation(allocation_pct: float, price: float) -> tuple[int, float]:
    """Clamps allocation_pct into [min, max] and converts it into a share
    count against the fixed starting_capital (not current equity -- avoids
    allocations silently growing/shrinking as the account compounds, which
    would make position sizes hard to reason about). Returns
    (shares, allocation_amount actually committed = shares * price).
    shares=0 means the price was too high for even the max allocation."""
    clamped_pct = max(
        settings.paper_trading_min_allocation_pct,
        min(allocation_pct, settings.paper_trading_max_allocation_pct),
    )
    budget = settings.paper_trading_starting_capital * clamped_pct / 100
    shares = int(budget // price) if price > 0 else 0
    return shares, shares * price


async def get_available_cash() -> float:
    """Simulated cash on hand right now: starting capital, minus capital
    tied up in currently-open positions, plus realized P&L already banked
    from closed ones. Recomputed from paper_positions each call rather than
    stored as a mutable balance -- single source of truth, no risk of the
    ledger drifting from the position log."""
    positions = await get_all_positions()
    total_bought = sum(p["shares"] * p["entry_price"] for p in positions)
    total_sold = sum(
        p["shares"] * p["exit_price"] for p in positions if p["status"] == "closed"
    )
    return settings.paper_trading_starting_capital - total_bought + total_sold


def simulate_portfolio_equity(scored_positions: list[dict]) -> dict:
    """Takes evaluate_paper_trades()'s "positions" list (already has
    current_price attached for open positions) and replays entry/exit cash
    flows to report a realistic equity view alongside -- not instead of --
    the size-agnostic win_rate/avg_return_pct stats.

    Caveat documented rather than hidden: the equity curve only has a data
    point at each position's close (there's no continuous intraday price
    history stored), so realized_max_drawdown_pct is computed from that
    closed-trade sequence, ignoring floating swings of positions that were
    open at the same time. A true continuous drawdown would need periodic
    equity snapshots, which this project doesn't take.
    """
    starting = settings.paper_trading_starting_capital
    total_bought = sum(p["shares"] * p["entry_price"] for p in scored_positions)
    total_sold = sum(
        p["shares"] * p["exit_price"] for p in scored_positions if p["status"] == "closed"
    )
    cash = starting - total_bought + total_sold
    open_value = sum(
        p["shares"] * p["current_price"]
        for p in scored_positions
        if p["status"] == "open" and p.get("current_price") is not None
    )
    current_equity = cash + open_value

    closed = sorted(
        (p for p in scored_positions if p["status"] == "closed"),
        key=lambda p: p.get("exit_date") or "",
    )
    curve = []
    running = starting
    peak = starting
    max_drawdown_pct = 0.0
    for p in closed:
        running += p["shares"] * (p["exit_price"] - p["entry_price"])
        peak = max(peak, running)
        if peak:
            max_drawdown_pct = max(max_drawdown_pct, (peak - running) / peak * 100)
        curve.append({"date": p.get("exit_date"), "equity": round(running, 0)})

    return {
        "starting_capital": starting,
        "current_cash": round(cash, 0),
        "open_positions_value": round(open_value, 0),
        "current_equity": round(current_equity, 0),
        "total_return_pct": round((current_equity - starting) / starting * 100, 2)
        if starting else None,
        "realized_max_drawdown_pct": round(max_drawdown_pct, 2),
        "equity_curve": curve,
    }
