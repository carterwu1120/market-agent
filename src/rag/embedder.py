"""Embedding utilities using sentence-transformers (local) or OpenAI.

Expected output dimensions per provider/model:
  local  BAAI/bge-m3              → 1024
  openai text-embedding-3-small   → 1536
  openai text-embedding-3-large   → 3072

EMBEDDING_DIM in models.py must match the active provider/model.
"""

import asyncio
from loguru import logger

from src.config import settings

# Dimension map — extend when adding new models
_OPENAI_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

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
        from src.memory.models import EMBEDDING_DIM
        openai_model = "text-embedding-3-small"
        expected_dim = _OPENAI_DIMS.get(openai_model, 1536)
        if expected_dim != EMBEDDING_DIM:
            raise RuntimeError(
                f"EMBEDDING_DIM mismatch: models.py declares {EMBEDDING_DIM} but "
                f"{openai_model} produces {expected_dim}. Update EMBEDDING_DIM in models.py."
            )
        import openai
        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await client.embeddings.create(model=openai_model, input=texts)
        return [item.embedding for item in resp.data]

    raise ValueError(f"Unknown embedding provider: {settings.embedding_provider}")


async def embed_single(text: str) -> list[float]:
    results = await embed_texts([text])
    return results[0]
