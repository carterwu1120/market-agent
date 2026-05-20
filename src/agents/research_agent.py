"""Research Agent — LangGraph native ReAct loop for complex/comparative queries.

適合處理開放式問題，例如：
- 「比較半導體和航運哪個現在更值得投資？」
- 「幫我找機器人題材中技術面最強的股票」
- 「今天哪個類股表現最好？」

LLM 自主決定呼叫哪些工具、呼叫幾次，直到有足夠資訊回答。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode
from loguru import logger

from src.agents.state import AgentState
from src.llm import get_langchain_llm
from src.tools.sector_data import get_sector_symbols
from src.tools.theme_search import search_theme_stocks
from src.tools.stock_data import get_technical_indicators, get_fundamental_data
from src.tools.company_insight import get_company_insights

MAX_ITERATIONS = 6  # 防止無限循環


# ── Tool 定義 ────────────────────────────────────────────────────────────────

@tool
async def sector_lookup(keyword: str) -> str:
    """查詢 TWSE 官方產業類股的成份股。適用於半導體、航運、金融、鋼鐵等官方產業關鍵字。"""
    result = await get_sector_symbols(keyword, max_symbols=8)
    symbols = result.get("symbols", [])
    names = result.get("sector_names", [])
    if not symbols:
        return f"找不到「{keyword}」相關的官方產業類股"
    return f"產業：{', '.join(names)} | 代表股：{', '.join(symbols)}"


@tool
async def theme_lookup(keyword: str) -> str:
    """查詢市場主題/概念股。適用於機器人、元宇宙、低軌衛星、AI、電動車等題材關鍵字。"""
    result = await search_theme_stocks(keyword, max_symbols=8)
    symbols = result.get("symbols", [])
    matched = result.get("matched_concept", keyword)
    if not symbols:
        return f"找不到「{keyword}」相關概念股"
    return f"概念：{matched} | 個股：{', '.join(symbols)}"


@tool
async def technical_analysis(symbol: str) -> str:
    """查詢個股技術面指標：現價、RSI、MACD、均線、乖離率、布林帶。symbol 格式：2330.TW"""
    ind = await get_technical_indicators(symbol)
    if ind.get("error"):
        return f"{symbol} 技術面資料取得失敗：{ind['error']}"
    return (
        f"{symbol} | 現價: {ind.get('close')} | RSI: {ind.get('rsi_14')} | "
        f"MACD: {ind.get('macd')} | MA20: {ind.get('sma_20')} | MA60: {ind.get('sma_60')} | "
        f"乖離率(20): {ind.get('bias_20')}% | 乖離率(60): {ind.get('bias_60')}% | "
        f"布林上軌: {ind.get('bb_upper')} | 下軌: {ind.get('bb_lower')}"
    )


@tool
async def fundamental_analysis(symbol: str) -> str:
    """查詢個股基本面：本益比、股價淨值比、EPS、ROE、營收成長、分析師評等。symbol 格式：2330.TW"""
    data = await get_fundamental_data(symbol)
    if data.get("error"):
        return f"{symbol} 基本面資料取得失敗：{data['error']}"
    return (
        f"{symbol} {data.get('company_name', '')} | "
        f"PE: {data.get('pe_ratio')} | PB: {data.get('pb_ratio')} | "
        f"EPS: {data.get('eps_ttm')} | ROE: {data.get('roe')} | "
        f"營收成長: {data.get('revenue_growth')} | 毛利率: {data.get('gross_margin')} | "
        f"目標價: {data.get('analyst_target')} | 評等: {data.get('analyst_recommendation')}"
    )


@tool
async def company_news(symbol: str) -> str:
    """查詢個股法說會、技術突破、產品新聞。symbol 格式：2330.TW"""
    result = await get_company_insights(symbol, max_articles=5)
    articles = result.get("articles", [])
    if not articles:
        return f"{symbol} 暫無相關法說會或技術新聞"
    lines = [f"{result.get('company_name', symbol)} 最新消息："]
    for a in articles:
        lines.append(f"- {a['title']}")
    return "\n".join(lines)


# ── ReAct 系統提示 ──────────────────────────────────────────────────────────

REACT_SYSTEM = """你是一個台股研究分析師，可以使用以下工具收集資料：

- sector_lookup(keyword): 查 TWSE 官方產業類股（半導體、航運、金融…）
- theme_lookup(keyword): 查市場主題/概念股（機器人、AI、電動車…）
- technical_analysis(symbol): 查個股技術面（RSI、MACD、均線、乖離率…）
- fundamental_analysis(symbol): 查個股基本面（PE、ROE、營收成長…）
- company_news(symbol): 查個股法說會與技術新聞

策略：
1. 先用 sector_lookup 或 theme_lookup 找出相關個股代碼
2. 再用 technical_analysis / fundamental_analysis 分析具體數據
3. 收集足夠資料後，直接輸出分析結論，不要再呼叫工具
4. 回答使用繁體中文，每個判斷都要引用工具回傳的數據
5. 嚴禁使用工具之外的自身知識補充數字或技術描述
"""


# ── Agent Node ───────────────────────────────────────────────────────────────

_TOOLS = [sector_lookup, theme_lookup, technical_analysis, fundamental_analysis, company_news]
_tool_node = ToolNode(_TOOLS)


def _extract_text(content: Any) -> str:
    """Normalize LLM response content to plain string.
    Gemini returns list of content blocks: [{'type': 'text', 'text': '...'}]
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    return str(content)


async def research_agent_node(state: AgentState) -> dict:
    """ReAct loop：LLM 自主決定呼叫哪些工具直到得出結論。"""
    logger.info("ResearchAgent: starting ReAct loop")

    llm = get_langchain_llm().bind_tools(_TOOLS)

    # Inject conversation history so LLM can do context-aware research
    history_messages = []
    for m in (state.conversation_history or [])[-6:]:
        if m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content", "")
        meta = m.get("meta") or {}
        symbols = meta.get("symbols", [])
        if symbols and m["role"] == "assistant":
            content = f"[分析標的: {', '.join(symbols)}]\n{content}"
        if m["role"] == "user":
            history_messages.append(HumanMessage(content=content))
        else:
            history_messages.append(AIMessage(content=content))

    messages = [
        SystemMessage(content=REACT_SYSTEM),
        *history_messages,
        HumanMessage(content=state.user_message),
    ]

    iterations = 0
    while iterations < MAX_ITERATIONS:
        iterations += 1
        response: AIMessage = await llm.ainvoke(messages)
        messages.append(response)

        # 沒有 tool_calls → LLM 完成推理，輸出最終答案
        if not response.tool_calls:
            logger.info(f"ResearchAgent: done after {iterations} iterations")
            return {
                "final_report": _extract_text(response.content),
                "conclusion": _extract_text(response.content)[-600:],
                "sources": [],
            }

        # 執行 tool calls
        logger.info(f"ResearchAgent iter {iterations}: calling {[tc['name'] for tc in response.tool_calls]}")
        tool_results = await _tool_node.ainvoke({"messages": messages})
        # ToolNode 回傳 {"messages": [...ToolMessage...]}
        messages.extend(tool_results["messages"])

    # 超過最大迭代次數，強制用目前資訊生成報告
    logger.warning("ResearchAgent: max iterations reached, forcing conclusion")
    final = await llm.ainvoke([
        *messages,
        HumanMessage(content="根據以上收集到的資料，請直接給出最終分析結論。"),
    ])
    text = _extract_text(final.content)
    return {"final_report": text, "conclusion": text[-600:], "sources": []}
