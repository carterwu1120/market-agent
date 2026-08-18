"""Embedding utilities using sentence-transformers (local).

Expected output dimension: BAAI/bge-m3 → 1024.
EMBEDDING_DIM in models.py must match the active model.
"""

import asyncio
from loguru import logger

from src.config import settings

_model = None


def _get_local_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {settings.embedding_model}")
        _model = SentenceTransformer(settings.embedding_model)
    return _model


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts, returning list of float vectors."""
    if not texts:
        return []

    model = _get_local_model()
    vectors = await asyncio.to_thread(
        lambda: model.encode(texts, normalize_embeddings=True).tolist()
    )
    return vectors


async def embed_single(text: str) -> list[float]:
    results = await embed_texts([text])
    return results[0]
