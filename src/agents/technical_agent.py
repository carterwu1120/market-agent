"""Technical Analysis Agent — computes indicators for target symbols."""

import asyncio
from loguru import logger

from src.agents.state import AgentState
from src.tools.stock_data import get_stock_price, get_technical_indicators
from src.tools.company_insight import get_company_insights


async def technical_agent_node(state: AgentState) -> dict:
    """LangGraph node: fetch price + technical indicators + company insights for each symbol."""
    symbols = state.target_symbols
    if not symbols:
        logger.info("TechnicalAgent: no symbols, skipping")
        return {"technical_data": [], "insight_data": []}

    logger.info(f"TechnicalAgent: analyzing {symbols}")

    async def _analyze(sym: str) -> dict:
        price, indicators, insights = await asyncio.gather(
            get_stock_price(sym),
            get_technical_indicators(sym),
            get_company_insights(sym, max_articles=6),
        )
        return {
            "symbol": sym,
            "price": price,
            "indicators": indicators,
            "insights": insights,
        }

    results = await asyncio.gather(*[_analyze(s) for s in symbols], return_exceptions=True)
    data = []
    insight_data = []
    sources = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"TechnicalAgent error: {r}")
            continue
        data.append({"symbol": r["symbol"], "price": r["price"], "indicators": r["indicators"]})
        if r.get("insights", {}).get("articles"):
            insight_data.append(r["insights"])
        if r.get("price", {}).get("source"):
            sources.append(r["price"]["source"])

    return {"technical_data": data, "insight_data": insight_data, "sources": sources}
