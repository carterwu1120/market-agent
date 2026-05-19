"""Embedding utilities using sentence-transformers (local) or OpenAI."""

from typing import Union
import asyncio
import numpy as np
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

    if settings.embedding_provider == "local":
        model = _get_local_model()
        vectors = await asyncio.to_thread(
            lambda: model.encode(texts, normalize_embeddings=True).tolist()
        )
        return vectors

    elif settings.embedding_provider == "openai":
        import openai
        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await client.embeddings.create(
            model="text-embedding-3-small",
            input=texts,
        )
        return [item.embedding for item in resp.data]

    raise ValueError(f"Unknown embedding provider: {settings.embedding_provider}")


async def embed_single(text: str) -> list[float]:
    results = await embed_texts([text])
    return results[0]
