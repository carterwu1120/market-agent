"""Configured LLM backend facade."""

from src.config import settings
from src.llm_claude_code import (
    ALL_TOOL_NAMES as CLAUDE_ALL_TOOL_NAMES,
)
from src.llm_claude_code import (
    USER_FACING_TOOL_NAMES,
    claude_code_chat,
    claude_code_research,
)
from src.llm_codex import codex_chat, codex_research

ALL_TOOL_NAMES = CLAUDE_ALL_TOOL_NAMES


async def llm_chat(messages: list[dict], system: str = "", timeout: int = 180) -> str:
    if settings.llm_backend == "codex":
        return await codex_chat(messages, system, timeout)
    return await claude_code_chat(messages, system, timeout)


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
