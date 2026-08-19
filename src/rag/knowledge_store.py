"""RAG knowledge store — local SQLite + brute-force cosine similarity.

Used for technical analysis knowledge (RSI interpretation, MA strategies, etc.).
Corpus is small (a handful of docs), so loading every row and scoring it in
Python with numpy is simpler than standing up a real vector index.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from loguru import logger

from src.memory.store import _connect
from src.rag.embedder import embed_single, embed_texts


# ── Knowledge base ingestion ─────────────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into chunks.

    Priority: paragraph (blank line) → sentence (。？！) → word count fallback.
    """
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


def _already_ingested_sync(doc_id: str) -> bool:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id FROM knowledge_chunks WHERE doc_id = ? LIMIT 1", (doc_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _insert_chunks_sync(doc_id: str, meta: dict, chunks: list[str], embeddings: list[list[float]]) -> None:
    conn = _connect()
    try:
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(meta, ensure_ascii=False)
        for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            conn.execute(
                """
                INSERT INTO knowledge_chunks (doc_id, chunk_index, content, meta, embedding, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (doc_id, idx, chunk, meta_json, np.array(emb, dtype=np.float32).tobytes(), now),
            )
        conn.commit()
    finally:
        conn.close()


async def ingest_document(
    doc_id: str,
    content: str,
    meta: dict | None = None,
    chunk_size: int = 500,
) -> int:
    """Chunk and embed a document into the knowledge base. Returns number of chunks added."""
    chunks = _chunk_text(content, chunk_size)
    if not chunks:
        return 0

    if await asyncio.to_thread(_already_ingested_sync, doc_id):
        logger.info(f"Document '{doc_id}' already in knowledge base, skipping")
        return 0

    embeddings = await embed_texts(chunks)
    await asyncio.to_thread(_insert_chunks_sync, doc_id, meta or {}, chunks, embeddings)
    logger.info(f"Ingested '{doc_id}': {len(chunks)} chunks")
    return len(chunks)


async def ingest_directory(directory: str | Path) -> None:
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
        await ingest_document(doc_id=str(f), content=content, meta={"filename": f.name})


# ── Similarity search ────────────────────────────────────────────────────────

def _load_chunks_sync() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT doc_id, content, meta, embedding FROM knowledge_chunks").fetchall()
        return [
            {
                "doc_id": r["doc_id"],
                "content": r["content"],
                "meta": json.loads(r["meta"]),
                "embedding": np.frombuffer(r["embedding"], dtype=np.float32),
            }
            for r in rows
        ]
    finally:
        conn.close()


async def search_knowledge(query: str, top_k: int = 5, score_threshold: float = 0.5) -> list[dict]:
    """Brute-force cosine similarity search over the knowledge base."""
    query_emb = np.array(await embed_single(query), dtype=np.float32)
    query_norm = np.linalg.norm(query_emb)
    if query_norm == 0:
        return []

    chunks = await asyncio.to_thread(_load_chunks_sync)
    scored = []
    for c in chunks:
        vec_norm = np.linalg.norm(c["embedding"])
        if vec_norm == 0:
            continue
        score = float(np.dot(query_emb, c["embedding"]) / (query_norm * vec_norm))
        if score >= score_threshold:
            scored.append({"doc_id": c["doc_id"], "content": c["content"], "score": score, "meta": c["meta"]})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
