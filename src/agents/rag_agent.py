"""RAG Agent — retrieves relevant knowledge from pgvector before synthesis."""

from loguru import logger

from src.agents.state import AgentState
from src.memory.database import AsyncSessionFactory
from src.rag.knowledge_store import search_knowledge


async def rag_agent_node(state: AgentState) -> dict:
    query = state.user_message
    if state.target_symbols:
        query = f"{query} {' '.join(state.target_symbols)}"

    logger.info(f"RAGAgent: searching knowledge for '{query[:60]}'")

    try:
        # Quick connectivity check before loading the embedding model
        from sqlalchemy import text as sa_text
        async with AsyncSessionFactory() as session:
            await session.execute(sa_text("SELECT 1"))
    except Exception as exc:
        logger.warning(f"RAGAgent: DB unavailable, skipping ({exc})")
        return {"rag_context": []}

    try:
        async with AsyncSessionFactory() as session:
            results = await search_knowledge(session, query, top_k=5)
    except Exception as exc:
        logger.warning(f"RAGAgent search failed: {exc}")
        return {"rag_context": []}

    logger.info(f"RAGAgent: found {len(results)} relevant chunks")
    return {"rag_context": results}
