"""History Agent — queries stored historical stock data from PostgreSQL."""

import asyncio
from loguru import logger

from src.agents.state import AgentState
from src.memory.stock_store import query_stock_history


async def history_agent_node(state: AgentState) -> dict:
    symbols = state.target_symbols
    days = state.history_days or 7

    if not symbols:
        logger.warning("history_agent: no target symbols")
        return {"history_data": []}

    logger.info(f"history_agent: querying {symbols} for {days} days")
    results = await asyncio.gather(*[
        query_stock_history(sym, days=days) for sym in symbols
    ], return_exceptions=True)
    data = []
    for sym, r in zip(symbols, results):
        if isinstance(r, Exception):
            logger.warning(f"history_agent: {sym} failed: {r}")
            data.append({"symbol": sym, "error": str(r)})
        else:
            data.append(r)
    return {"history_data": data}
