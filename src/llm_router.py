"""Backend switch for single-shot LLM calls (LiteLLM vs Claude Code CLI).

Shared by orchestrator/market_agent/synthesizer/research_agent — all of
which pick between llm_chat() (LiteLLM) and claude_code_chat() based on
the same LLM_BACKEND setting.
"""

from __future__ import annotations

from src.config import settings


def use_claude_code_backend() -> bool:
    return settings.llm_backend == "claude_code"
