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


def main():
    setup_logging()
    backend_label = settings.llm_backend if settings.llm_backend == "claude_code" else f"{settings.llm_backend}/{settings.llm_model}"
    logger.info(f"Starting Market Agent | LLM: {backend_label}")

    async def _start():
        from src.memory.database import try_init_db
        await try_init_db()
        from src.bot.discord_bot import bot
        from src.config import settings
        await bot.start(settings.discord_bot_token)

    asyncio.run(_start())


if __name__ == "__main__":
    main()
