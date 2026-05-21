"""RAG knowledge store — pgvector similarity search.

Used for two purposes:
1. Technical analysis knowledge (RSI interpretation, MA strategies, etc.)
2. Cached news embeddings for duplicate detection and similarity search
"""

from __future__ import annotations
import hashlib
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from src.memory.models import KnowledgeChunk, NewsItem
from src.rag.embedder import embed_single, embed_texts


# ── Knowledge base ingestion ─────────────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into chunks.

    Priority: paragraph (blank line) → sentence (。？！) → word count fallback.
    """
    import re

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks = []
    for para in paragraphs:
        if len(para.split()) <= chunk_size:
            chunks.append(para)
            continue

        # Paragraph too long — split by sentence-ending punctuation
        sentences = [s.strip() for s in re.split(r'(?<=[。？！\.!\?])', para) if s.strip()]
        current: list[str] = []
        current_len = 0
        for sent in sentences:
            sent_len = len(sent.split())
            if current_len + sent_len > chunk_size and current:
                chunks.append(" ".join(current))
                current = []
                current_len = 0
            if sent_len > chunk_size:
                # Single sentence longer than chunk_size — word-count fallback
                words = sent.split()
                i = 0
                while i < len(words):
                    chunks.append(" ".join(words[i : i + chunk_size]))
                    i += chunk_size - overlap
            else:
                current.append(sent)
                current_len += sent_len
        if current:
            chunks.append(" ".join(current))

    return chunks


async def ingest_document(
    session: AsyncSession,
    doc_id: str,
    content: str,
    meta: dict | None = None,
    chunk_size: int = 500,
) -> int:
    """Chunk and embed a document into the knowledge base. Returns number of chunks added."""
    chunks = _chunk_text(content, chunk_size)
    if not chunks:
        return 0

    # Check if already ingested
    existing = await session.execute(
        select(KnowledgeChunk.id).where(KnowledgeChunk.doc_id == doc_id).limit(1)
    )
    if existing.scalar_one_or_none():
        logger.info(f"Document '{doc_id}' already in knowledge base, skipping")
        return 0

    embeddings = await embed_texts(chunks)
    for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        session.add(KnowledgeChunk(
            doc_id=doc_id,
            chunk_index=idx,
            content=chunk,
            meta=meta or {},
            embedding=emb,
        ))

    await session.commit()
    logger.info(f"Ingested '{doc_id}': {len(chunks)} chunks")
    return len(chunks)


async def ingest_directory(session: AsyncSession, directory: str | Path) -> None:
    """Ingest all .txt and .md files from a directory."""
    path = Path(directory)
    # pathlib does not support brace expansion — iterate each suffix separately
    files = [
        f for f in list(path.rglob("*.md")) + list(path.rglob("*.txt"))
        if f.name.upper() != "README.MD"
    ]
    if not files and any(f for f in path.iterdir() if f.name.upper() != "README.MD"):
        raise RuntimeError(f"No .md/.txt files found in {path} but directory is non-empty")
    for f in files:
        content = f.read_text(encoding="utf-8", errors="ignore")
        await ingest_document(session, doc_id=str(f), content=content, meta={"filename": f.name})


# ── Similarity search ────────────────────────────────────────────────────────

async def search_knowledge(
    session: AsyncSession,
    query: str,
    top_k: int = 5,
    score_threshold: float = 0.5,
) -> list[dict]:
    """Vector similarity search over the knowledge base."""
    query_emb = await embed_single(query)

    result = await session.execute(
        text("""
            SELECT id, doc_id, content, meta,
                   1 - (embedding <=> CAST(:embedding AS vector)) AS score
            FROM knowledge_chunks
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :k
        """),
        {"embedding": str(query_emb), "k": top_k},
    )

    rows = result.fetchall()
    return [
        {"doc_id": r.doc_id, "content": r.content, "score": r.score, "meta": r.meta}
        for r in rows
        if r.score >= score_threshold
    ]


async def search_news(
    session: AsyncSession,
    query: str,
    top_k: int = 10,
    score_threshold: float = 0.4,
) -> list[dict]:
    """Vector similarity search over cached news embeddings."""
    query_emb = await embed_single(query)

    result = await session.execute(
        text("""
            SELECT id, title, content, url, source, published_at,
                   1 - (embedding <=> CAST(:embedding AS vector)) AS score
            FROM news_items
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :k
        """),
        {"embedding": str(query_emb), "k": top_k},
    )

    rows = result.fetchall()
    return [
        {
            "title": r.title,
            "content": r.content,
            "url": r.url,
            "source": r.source,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "score": r.score,
        }
        for r in rows
        if r.score >= score_threshold
    ]
