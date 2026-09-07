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

from src.agents.daily_brief import run_daily_brief
from src.agents.research_agent import run_research
from src.llm import llm_chat
from src.tools.news_fetcher import _TICKER_NAMES

_BRIEF_KEYWORDS = ["早安", "盤前", "今日總結", "市場摘要", "每日簡報", "今天市場概況", "大盤今天"]
_TOPIC_KEYWORDS = [
    "半導體", "金融", "航運", "鋼鐵", "生技", "AI", "機器人",
    "電動車", "題材", "概念", "類股", "產業",
]
_BARE_TICKER_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_MAX_SYMBOLS_PER_RESEARCH_BATCH = 2

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
        raw = await llm_chat(
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
            result = await _run_research_batched(user_message, conversation_history or [])
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        logger.error(f"run_agent failed after {elapsed:.1f}s: {exc}", exc_info=True)
        error_message = str(exc)
        if _is_timeout_error(error_message):
            final_report = (
                "⚠️ AI 分析超過設定時間，本次未能完成回答。\n"
                "若問題包含多個標的，請拆成單檔或兩檔後再試。"
            )
        else:
            stage = _infer_failed_stage(error_message)
            final_report = (
                f"⚠️ 分析流程在「{stage}」階段發生錯誤，請稍後再試。\n"
                f"（如持續發生請聯繫管理員）"
            )
        return {
            "final_report": final_report,
            "error": error_message,
            "intent": intent,
            "target_symbols": [],
            "conclusion": "",
            "sources": [],
        }

    elapsed = time.perf_counter() - t0
    logger.info(f"run_agent completed in {elapsed:.1f}s")
    result["intent"] = intent
    return result


def extract_requested_symbols(user_message: str) -> list[str]:
    """Resolve explicit tickers and known company names, preserving order."""
    symbols = [f"{code}.TW" for code in _BARE_TICKER_RE.findall(user_message)]
    lowered = user_message.lower()
    for code, names in _TICKER_NAMES.items():
        if any(name.lower() in lowered for name in names):
            symbols.append(f"{code}.TW")
    return list(dict.fromkeys(symbols))


async def _run_research_batched(user_message: str, conversation_history: list[dict]) -> dict:
    """Split 3+ symbol questions so one failed symbol group cannot erase all output."""
    symbols = extract_requested_symbols(user_message)
    if len(symbols) <= _MAX_SYMBOLS_PER_RESEARCH_BATCH:
        return await run_research(user_message, conversation_history)

    batches = [
        symbols[index:index + _MAX_SYMBOLS_PER_RESEARCH_BATCH]
        for index in range(0, len(symbols), _MAX_SYMBOLS_PER_RESEARCH_BATCH)
    ]
    reports: list[str] = []
    conclusions: list[str] = []
    completed_symbols: list[str] = []
    failures: list[tuple[list[str], Exception]] = []
    total_cost = 0.0

    for index, batch in enumerate(batches, start=1):
        batch_prompt = (
            f"原始問題：{user_message}\n\n"
            f"這是第 {index}/{len(batches)} 批。只分析本批標的：{', '.join(batch)}。"
            "不要查詢或分析其他批次的標的；完整回答原始問題中與本批標的相關的部分。"
        )
        logger.info("Pipeline: research batch {}/{} symbols={}", index, len(batches), batch)
        try:
            batch_result = await run_research(batch_prompt, conversation_history)
        except Exception as exc:
            logger.warning("Research batch {}/{} failed: {}", index, len(batches), exc)
            failures.append((batch, exc))
            continue
        reports.append(batch_result.get("final_report", ""))
        conclusions.append(batch_result.get("conclusion", ""))
        completed_symbols.extend(batch_result.get("target_symbols", []) or batch)
        total_cost += float(batch_result.get("cost_usd", 0.0))

    if not reports:
        raise failures[0][1]

    if failures:
        failed_symbols = ", ".join(symbol for batch, _ in failures for symbol in batch)
        reports.append(
            f"⚠️ 部分分析未完成：{failed_symbols}。其他已完成結果仍保留；"
            "可稍後針對未完成標的單獨詢問。"
        )

    result = {
        "final_report": "\n\n---\n\n".join(report for report in reports if report),
        "conclusion": "；".join(item for item in conclusions if item),
        "sources": [],
        "target_symbols": list(dict.fromkeys(completed_symbols)),
        "cost_usd": total_cost,
    }
    if failures:
        result["error"] = "one or more research batches failed"
    return result


def _is_timeout_error(error_msg: str) -> bool:
    msg = error_msg.lower()
    return "timed out" in msg or "timeout" in msg or "逾時" in msg or "超時" in msg


def _infer_failed_stage(error_msg: str) -> str:
    msg = error_msg.lower()
    if _is_timeout_error(msg):
        return "AI 分析逾時"
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
