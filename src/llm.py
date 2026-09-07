"""Configured LLM backend facade."""

from src.config import settings
from src.llm_claude_code import (
    ALL_TOOL_NAMES as CLAUDE_ALL_TOOL_NAMES,
)
from src.llm_claude_code import (
    PAPER_TRADING_TOOL_NAMES as CLAUDE_PAPER_TRADING_TOOL_NAMES,
)
from src.llm_claude_code import (
    USER_FACING_TOOL_NAMES,
    claude_code_chat,
    claude_code_chat_with_usage,
    claude_code_research,
)
from src.llm_codex import codex_chat, codex_research

ALL_TOOL_NAMES = CLAUDE_ALL_TOOL_NAMES
PAPER_TRADING_TOOL_NAMES = CLAUDE_PAPER_TRADING_TOOL_NAMES


async def llm_chat(messages: list[dict], system: str = "", timeout: int = 180) -> str:
    if settings.llm_backend == "codex":
        return await codex_chat(messages, system, timeout)
    return await claude_code_chat(messages, system, timeout)


async def llm_chat_with_usage(
    messages: list[dict], system: str = "", timeout: int = 180
) -> dict:
    """Non-agentic chat plus cost metadata when the selected CLI exposes it."""
    if settings.llm_backend == "codex":
        result = await codex_chat(messages, system, timeout)
        return {"result": result, "cost_usd": 0.0}
    return await claude_code_chat_with_usage(messages, system, timeout)


async def llm_research(
    system: str,
    history: list[dict],
    user_message: str,
    tool_names: list[str] = USER_FACING_TOOL_NAMES,
    timeout: int = 180,
) -> dict:
    if settings.llm_backend == "codex":
        return await codex_research(system, history, user_message, tool_names, timeout)
    return await claude_code_research(system, history, user_message, tool_names, timeout)
