"""LangGraph multi-agent pipeline definition.

Flow:
  orchestrator
      │
      ├── daily_brief → [news, social, rag] → synthesizer
      │
      ├── stock_query → [news, technical, fundamental, chip, social, rag] → synthesizer
      │
      └── history_query → history_agent → synthesizer

Parallel branches use Send for fan-out. All branches converge at synthesizer.
"""

from langgraph.graph import StateGraph, END
from loguru import logger
from langgraph.graph.state import CompiledStateGraph

from src.agents.state import AgentState
from src.agents.orchestrator import orchestrator_node
from src.agents.news_agent import news_agent_node
from src.agents.technical_agent import technical_agent_node
from src.agents.fundamental_agent import fundamental_agent_node
from src.agents.chip_agent import chip_agent_node
from src.agents.social_agent import social_agent_node
from src.agents.rag_agent import rag_agent_node
from src.agents.synthesizer import synthesizer_node
from src.agents.research_agent import research_agent_node
from src.agents.market_agent import market_agent_node
from src.agents.history_agent import history_agent_node


_ANALYSIS_AGENTS = [
    "technical_agent", "fundamental_agent", "chip_agent", "social_agent", "rag_agent",
]
_ALL_DATA_AGENTS = ["news_agent"] + _ANALYSIS_AGENTS


def _route_after_orchestrator(state: AgentState) -> list[str]:
    intent = state.intent

    if intent == "research":
        return ["research_agent"]

    if intent == "history_query":
        return ["history_agent"]

    news_agents = [] if state.news_cached else ["news_agent"]

    if intent in ("stock_query", "sector_query", "theme_query", "follow_up"):
        if state.target_symbols:
            return news_agents + _ANALYSIS_AGENTS
        return news_agents + ["social_agent", "rag_agent"]
    elif intent == "daily_brief":
        # Phase 1: news first (market_agent runs after news_agent)
        # If cache hit, go straight to market_agent
        if state.news_cached:
            return ["market_agent"]
        return ["news_agent"]
    else:
        return news_agents + ["social_agent", "rag_agent"]


def _route_after_news(state: AgentState) -> str:
    """news_agent → market_agent for daily_brief, synthesizer otherwise."""
    if state.intent == "daily_brief":
        return "market_agent"
    return "synthesizer"


def _route_after_market(state: AgentState) -> list[str]:
    """market_agent always fans out to full analysis agents."""
    return _ANALYSIS_AGENTS


def build_graph() -> CompiledStateGraph:
    builder = StateGraph(AgentState)

    # Add all nodes
    builder.add_node("orchestrator", orchestrator_node)
    builder.add_node("research_agent", research_agent_node)
    builder.add_node("news_agent", news_agent_node)
    builder.add_node("market_agent", market_agent_node)
    builder.add_node("technical_agent", technical_agent_node)
    builder.add_node("fundamental_agent", fundamental_agent_node)
    builder.add_node("chip_agent", chip_agent_node)
    builder.add_node("social_agent", social_agent_node)
    builder.add_node("rag_agent", rag_agent_node)
    builder.add_node("synthesizer", synthesizer_node)
    builder.add_node("history_agent", history_agent_node)

    builder.set_entry_point("orchestrator")

    # Orchestrator fan-out
    _ROUTE_TARGETS = _ALL_DATA_AGENTS + ["research_agent", "market_agent", "history_agent"]
    builder.add_conditional_edges(
        "orchestrator",
        _route_after_orchestrator,
        {node: node for node in _ROUTE_TARGETS},
    )

    # news_agent: daily_brief → market_agent, others → synthesizer
    builder.add_conditional_edges(
        "news_agent",
        _route_after_news,
        {"market_agent": "market_agent", "synthesizer": "synthesizer"},
    )

    # market_agent: fan-out to all analysis agents
    builder.add_conditional_edges(
        "market_agent",
        _route_after_market,
        {node: node for node in _ANALYSIS_AGENTS},
    )

    # history_agent → synthesizer
    builder.add_edge("history_agent", "synthesizer")

    # research_agent → END
    builder.add_edge("research_agent", END)

    # analysis agents → synthesizer
    for node in _ANALYSIS_AGENTS:
        builder.add_edge(node, "synthesizer")

    builder.add_edge("synthesizer", END)

    return builder.compile()


# Singleton graph instance
_graph: CompiledStateGraph | None = None


def get_graph() -> CompiledStateGraph:
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


async def run_agent(
    user_message: str,
    user_id: str,
    channel_id: str,
    conversation_history: list[dict] | None = None,
) -> AgentState:
    """Entry point called by the Discord bot."""
    import time
    graph = get_graph()
    initial_state = AgentState(
        user_message=user_message,
        user_id=user_id,
        channel_id=channel_id,
        conversation_history=conversation_history or [],
    )
    t0 = time.perf_counter()
    try:
        result = await graph.ainvoke(initial_state)
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        logger.error(f"run_agent failed after {elapsed:.1f}s: {exc}", exc_info=True)
        # Return a minimal state with a user-friendly error embedded
        stage = _infer_failed_stage(str(exc))
        initial_state.final_report = (
            f"⚠️ 分析流程在「{stage}」階段發生錯誤，請稍後再試。\n"
            f"（如持續發生請聯繫管理員）"
        )
        initial_state.error = str(exc)
        return initial_state
    elapsed = time.perf_counter() - t0
    logger.info(f"run_agent completed in {elapsed:.1f}s")
    return result


def _infer_failed_stage(error_msg: str) -> str:
    """Map common error patterns to a human-readable pipeline stage name."""
    msg = error_msg.lower()
    if "synthesizer" in msg or "llm" in msg or "litellm" in msg:
        return "報告生成"
    if "technical" in msg or "price" in msg or "yahoo" in msg:
        return "技術面數據擷取"
    if "fundamental" in msg:
        return "基本面數據擷取"
    if "chip" in msg or "twse" in msg:
        return "籌碼面數據擷取"
    if "news" in msg or "rss" in msg:
        return "新聞擷取"
    if "rag" in msg or "embedding" in msg or "pgvector" in msg:
        return "知識庫查詢"
    if "orchestrator" in msg or "intent" in msg:
        return "意圖分析"
    if "redis" in msg:
        return "快取讀寫"
    return "資料處理"
