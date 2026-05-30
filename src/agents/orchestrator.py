"""Orchestrator Agent — routes incoming user messages to the right sub-agents.

Only classifies daily_brief vs everything-else. All non-brief queries go to
the ReAct agent which decides which tools to call.
"""

import re
from loguru import logger

from src.agents.state import AgentState
from src.llm import llm_chat
from src.memory.news_cache import has_fresh_news

_BRIEF_KEYWORDS = ["早安", "盤前", "今日總結", "市場摘要", "每日簡報", "今天市場概況", "大盤今天"]
_TOPIC_KEYWORDS = ["半導體", "金融", "航運", "鋼鐵", "生技", "AI", "機器人", "電動車", "題材", "概念", "類股", "產業"]

# Known Taiwan company name → ticker (for deterministic resolution)
TW_COMPANY_TO_CODE: dict[str, str] = {
    "台積電": "2330.TW", "tsmc": "2330.TW",
    "聯發科": "2454.TW", "mediatek": "2454.TW",
    "鴻海": "2317.TW", "foxconn": "2317.TW",
    "台達電": "2308.TW",
    "廣達": "2382.TW",
    "緯創": "3231.TW",
    "日月光": "3711.TW",
    "聯電": "2303.TW", "umc": "2303.TW",
    "中華電": "2412.TW",
    "富邦金": "2881.TW",
    "國泰金": "2882.TW",
    "玉山金": "2884.TW",
}

INTENT_SYSTEM = """You are a financial assistant router. Analyze the user message and return JSON.

Only classify as "daily_brief" if the user explicitly wants today's overall market summary,
morning brief, or broad investment overview with no specific stock or topic in mind.

Everything else — stock queries, sector questions, historical data, comparisons, follow-ups,
research — return "react".

Return ONLY valid JSON:
{"intent": "daily_brief" | "react", "reasoning": "brief reason"}
"""


async def orchestrator_node(state: AgentState) -> dict:
    """LangGraph node: classify daily_brief vs react."""
    msg = state.user_message
    logger.info(f"Orchestrator: processing message '{msg[:80]}'")

    # Deterministic symbol extraction (bare tickers + company name lookup)
    bare_codes = re.findall(r"\b(\d{4})(?:\.TW)?\b", msg)
    pre_symbols = [f"{c}.TW" for c in bare_codes]
    name_symbols = [code for name, code in TW_COMPANY_TO_CODE.items() if name.lower() in msg.lower()]
    resolved_symbols = list(dict.fromkeys(pre_symbols + name_symbols))

    # Rule-based brief detection — only when no stock, company, or topic is mentioned
    has_topic = any(kw in msg for kw in _TOPIC_KEYWORDS)
    if not resolved_symbols and not has_topic and any(kw in msg for kw in _BRIEF_KEYWORDS):
        intent = "daily_brief"
    else:
        try:
            import json
            raw = await llm_chat(
                messages=[
                    {"role": "system", "content": INTENT_SYSTEM},
                    {"role": "user", "content": msg},
                ],
                temperature=0.1,
            )
            raw = re.sub(r"```(?:json)?|```", "", raw).strip()
            parsed = json.loads(raw)
            intent = parsed.get("intent", "react")
            if intent not in ("daily_brief", "react"):
                intent = "react"
        except Exception as exc:
            logger.warning(f"Orchestrator LLM call failed: {exc}")
            intent = "react"

    # Check if news cache is still fresh so graph can skip news_agent
    news_cached = await has_fresh_news()

    logger.info(f"Orchestrator → intent={intent}, symbols={resolved_symbols}, news_cached={news_cached}")
    return {
        "intent": intent,
        "news_cached": news_cached,
        "target_symbols": resolved_symbols,
        "sector_query": "",
        "sector_names": [],
        "theme_articles": [],
        "history_days": 7,
    }
