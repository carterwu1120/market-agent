"""Fundamental Analysis Agent — fetches financial ratios."""

import asyncio
from loguru import logger

from src.agents.state import AgentState
from src.tools.stock_data import get_fundamental_data


async def fundamental_agent_node(state: AgentState) -> dict:
    symbols = state.target_symbols
    if not symbols:
        return {"fundamental_data": []}

    logger.info(f"FundamentalAgent: analyzing {symbols}")
    results = await asyncio.gather(*[get_fundamental_data(s) for s in symbols], return_exceptions=True)

    data = []
    sources = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"FundamentalAgent error: {r}")
            continue
        data.append(r)
        if r.get("source"):
            sources.append(r["source"])

    return {"fundamental_data": data, "sources": sources}
