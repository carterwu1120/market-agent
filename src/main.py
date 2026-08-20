"""Application entrypoint."""

import asyncio
import sys

from loguru import logger

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
    logger.info("Starting Market Agent | LLM: claude_code")

    async def _start():
        from src.bot.discord_bot import bot
        from src.config import settings
        await bot.start(settings.discord_bot_token)

    asyncio.run(_start())


if __name__ == "__main__":
    main()
