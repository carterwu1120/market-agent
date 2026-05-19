"""Orchestrator Agent — routes incoming user messages to the right sub-agents.

Uses LLM to classify intent and extract ticker symbols from natural language.
"""

import json
import re
from loguru import logger

from src.agents.state import AgentState
from src.llm import llm_chat
from src.tools.sector_data import detect_sector_query, get_sector_symbols

# Known Taiwan ticker lookup (expandable)
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

Intents:
- "daily_brief": User wants today's market summary or investment recommendations
- "stock_query": User is asking about specific stock(s) by name or ticker code
- "sector_query": User is asking about a sector/industry (e.g. 半導體, 傳產, 石油, 金融)
- "follow_up": User is asking for more details on the previous response
- "unknown": Cannot determine

Extract Taiwan stock codes from company names using your knowledge.
If the user refers to a stock mentioned in the conversation history, include it in symbols.
For sector_query, do NOT extract individual symbols — just set intent to sector_query.

Return ONLY valid JSON:
{"intent": "<intent>", "symbols": ["2330.TW", ...], "reasoning": "brief reason"}
"""


async def orchestrator_node(state: AgentState) -> dict:
    """LangGraph node: classify intent and extract symbols."""
    msg = state.user_message
    logger.info(f"Orchestrator: processing message '{msg[:80]}'")

    # Quick lookup for bare ticker codes like "2330" or "2330.TW"
    bare_codes = re.findall(r"\b(\d{4})(?:\.TW)?\b", msg)
    pre_symbols = [f"{c}.TW" for c in bare_codes]

    # LLM-based intent classification — include recent history for follow-up context
    history_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in (state.conversation_history or [])[-6:]  # last 3 turns
        if m.get("role") in ("user", "assistant")
    ]
    try:
        raw = await llm_chat(
            messages=[
                {"role": "system", "content": INTENT_SYSTEM},
                *history_messages,
                {"role": "user", "content": msg},
            ],
            temperature=0.1,
        )
        # Strip markdown fences if present
        raw = re.sub(r"```(?:json)?|```", "", raw).strip()
        parsed = json.loads(raw)
        intent = parsed.get("intent", "unknown")
        llm_symbols = parsed.get("symbols", [])
    except Exception as exc:
        logger.warning(f"Orchestrator LLM call failed: {exc}")
        intent = "daily_brief" if not pre_symbols else "stock_query"
        llm_symbols = []

    # Also check company name lookup
    name_symbols = [
        code for name, code in TW_COMPANY_TO_CODE.items()
        if name.lower() in msg.lower()
    ]

    all_symbols = list(dict.fromkeys(pre_symbols + llm_symbols + name_symbols))

    if all_symbols and intent == "daily_brief":
        intent = "stock_query"

    # ── Sector detection ──────────────────────────────────────────────────────
    # Override intent if sector keyword found and no explicit tickers
    sector_kw = detect_sector_query(msg)
    if sector_kw and not all_symbols:
        intent = "sector_query"

    sector_query_str = ""
    resolved_sector_names: list[str] = []
    if intent == "sector_query":
        query_str = sector_kw or msg
        sector_result = await get_sector_symbols(query_str, max_symbols=8)
        all_symbols = sector_result.get("symbols", [])
        resolved_sector_names = sector_result.get("sector_names", [])
        sector_query_str = query_str
        logger.info(f"Sector resolved: {resolved_sector_names} → {len(all_symbols)} symbols")
        if sector_result.get("error"):
            logger.warning(f"Sector lookup error: {sector_result['error']}")

    logger.info(f"Orchestrator → intent={intent}, symbols={all_symbols}")
    return {
        "intent": intent,
        "target_symbols": all_symbols,
        "sector_query": sector_query_str,
        "sector_names": resolved_sector_names,
    }
