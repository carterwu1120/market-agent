"""Application entrypoint."""

import asyncio
from loguru import logger
import sys

from src.config import settings


def setup_logging():
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - {message}",
    )
    logger.add("logs/market_agent.log", rotation="10 MB", retention="7 days", level="DEBUG")


async def pull_ollama_model():
    """Ensure the configured Ollama model is available."""
    if settings.llm_provider != "ollama":
        return
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            models = [m["name"] for m in resp.json().get("models", [])]
            if settings.llm_model not in models:
                logger.info(f"Pulling Ollama model: {settings.llm_model}")
                await client.post(
                    f"{settings.ollama_base_url}/api/pull",
                    json={"name": settings.llm_model},
                    timeout=600,
                )
    except Exception as exc:
        logger.warning(f"Could not verify Ollama model: {exc}")


def main():
    setup_logging()
    logger.info(f"Starting Market Agent | LLM: {settings.llm_provider}/{settings.llm_model}")

    async def _start():
        await pull_ollama_model()
        from src.bot.discord_bot import bot
        from src.config import settings
        await bot.start(settings.discord_bot_token)

    asyncio.run(_start())


if __name__ == "__main__":
    main()
