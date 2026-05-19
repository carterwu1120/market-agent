"""Chip (籌碼面) Agent — three-institution trading data + margin trading."""

import asyncio
from loguru import logger

from src.agents.state import AgentState
from src.tools.chip_data import get_institutional_trading, get_margin_trading


async def chip_agent_node(state: AgentState) -> dict:
    symbols = state.target_symbols
    if not symbols:
        return {"chip_data": []}

    logger.info(f"ChipAgent: fetching chip data for {symbols}")

    async def _fetch(sym: str) -> dict:
        institutional, margin = await asyncio.gather(
            get_institutional_trading(sym),
            get_margin_trading(sym),
        )
        return {"symbol": sym, "institutional": institutional, "margin": margin}

    results = await asyncio.gather(*[_fetch(s) for s in symbols], return_exceptions=True)
    data = []
    sources = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"ChipAgent error: {r}")
            continue
        data.append(r)
        if r.get("institutional", {}).get("source"):
            sources.append(r["institutional"]["source"])

    return {"chip_data": data, "sources": sources}
