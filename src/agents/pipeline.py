"""Intent classification and top-level dispatch: daily_brief vs react.

daily_brief (deterministic, fixed data fan-out) vs react (Claude Code decides
which MCP tools to call) — see docs/adr/0001-drop-langgraph-delegate-to-claude-code.md
for why there's no LangGraph here.
"""

from __future__ import annotations

import json
import re
import time

from loguru import logger

from src.llm_claude_code import claude_code_chat
from src.agents.daily_brief import run_daily_brief
from src.agents.research_agent import run_research

_BRIEF_KEYWORDS = ["早安", "盤前", "今日總結", "市場摘要", "每日簡報", "今天市場概況", "大盤今天"]
_TOPIC_KEYWORDS = ["半導體", "金融", "航運", "鋼鐵", "生技", "AI", "機器人", "電動車", "題材", "概念", "類股", "產業"]
_BARE_TICKER_RE = re.compile(r"\b\d{4}\b")

INTENT_SYSTEM = """You are a financial assistant router. Analyze the user message and return JSON.

Only classify as "daily_brief" if the user explicitly wants today's overall market summary,
morning brief, or broad investment overview with no specific stock or topic in mind.

Everything else — stock queries, sector questions, historical data, comparisons, follow-ups,
research — return "react".

Return ONLY valid JSON:
{"intent": "daily_brief" | "react", "reasoning": "brief reason"}
"""


async def classify_intent(user_message: str) -> str:
    """Returns "daily_brief" or "react". Rule-based fast path, LLM fallback."""
    has_topic = any(kw in user_message for kw in _TOPIC_KEYWORDS)
    has_symbol = bool(_BARE_TICKER_RE.search(user_message))
    if not has_symbol and not has_topic and any(kw in user_message for kw in _BRIEF_KEYWORDS):
        return "daily_brief"

    try:
        raw = await claude_code_chat(
            messages=[{"role": "user", "content": user_message}],
            system=INTENT_SYSTEM,
        )
        raw = re.sub(r"```(?:json)?|```", "", raw).strip()
        parsed = json.loads(raw)
        intent = parsed.get("intent", "react")
        return intent if intent in ("daily_brief", "react") else "react"
    except Exception as exc:
        logger.warning(f"Intent classification LLM call failed: {exc}")
        return "react"


async def run_agent(
    user_message: str,
    user_id: str,
    channel_id: str,
    conversation_history: list[dict] | None = None,
) -> dict:
    """Entry point called by the Discord bot, CLI, and scheduler."""
    t0 = time.perf_counter()
    intent = await classify_intent(user_message)
    logger.info(f"Pipeline: intent={intent}")

    try:
        if intent == "daily_brief":
            result = await run_daily_brief(user_message)
        else:
            result = await run_research(user_message, conversation_history or [])
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        logger.error(f"run_agent failed after {elapsed:.1f}s: {exc}", exc_info=True)
        stage = _infer_failed_stage(str(exc))
        return {
            "final_report": (
                f"⚠️ 分析流程在「{stage}」階段發生錯誤，請稍後再試。\n"
                f"（如持續發生請聯繫管理員）"
            ),
            "error": str(exc),
            "intent": intent,
            "target_symbols": [],
            "conclusion": "",
            "sources": [],
        }

    elapsed = time.perf_counter() - t0
    logger.info(f"run_agent completed in {elapsed:.1f}s")
    result["intent"] = intent
    return result


def _infer_failed_stage(error_msg: str) -> str:
    msg = error_msg.lower()
    if "synthesizer" in msg or "llm" in msg or "claude" in msg:
        return "報告生成"
    if "technical" in msg or "price" in msg or "yahoo" in msg:
        return "技術面數據擷取"
    if "fundamental" in msg:
        return "基本面數據擷取"
    if "chip" in msg or "twse" in msg:
        return "籌碼面數據擷取"
    if "news" in msg or "rss" in msg:
        return "新聞擷取"
    if "rag" in msg or "embedding" in msg:
        return "知識庫查詢"
    if "intent" in msg:
        return "意圖分析"
    if "sqlite" in msg:
        return "快取讀寫"
    return "資料處理"
