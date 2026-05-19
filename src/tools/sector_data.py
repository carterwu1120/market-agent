"""產業/類股查詢工具：從 TWSE ISIN 清單取得成份股。

Primary: 動態從 TWSE 抓取（B 方案）
Fallback: 硬編碼代表股清單（A 方案）
"""

from __future__ import annotations
import asyncio
from functools import lru_cache
from typing import Any

import httpx
from bs4 import BeautifulSoup
from loguru import logger

# ── A 方案：fallback 清單（代表性大市值股）────────────────────────────────────
SECTOR_FALLBACK: dict[str, list[str]] = {
    "半導體業": ["2330.TW", "2454.TW", "2303.TW", "3711.TW", "2379.TW", "3034.TW"],
    "電子零組件業": ["2317.TW", "2382.TW", "3231.TW", "2354.TW", "2308.TW"],
    "電腦及週邊設備業": ["2382.TW", "3231.TW", "2324.TW", "2353.TW"],
    "通信網路業": ["2412.TW", "3045.TW", "4904.TW"],
    "金融保險業": ["2881.TW", "2882.TW", "2884.TW", "2886.TW", "2891.TW"],
    "油電燃氣業": ["1326.TW", "9945.TW", "8926.TW"],
    "鋼鐵工業": ["2002.TW", "2006.TW", "2008.TW"],
    "塑膠工業": ["1301.TW", "1303.TW", "1304.TW", "1305.TW"],
    "化學工業": ["1326.TW", "1314.TW", "1710.TW"],
    "生技醫療業": ["4711.TW", "1736.TW", "6547.TW", "4174.TW"],
    "食品工業": ["1216.TW", "1210.TW", "1229.TW"],
    "航運業": ["2609.TW", "2615.TW", "2603.TW", "2610.TW"],
    "建材營造業": ["2501.TW", "2511.TW", "2542.TW"],
    "電機機械": ["1504.TW", "2630.TW", "1590.TW"],
    "光電業": ["3481.TW", "2409.TW", "3008.TW"],
    "其他電子業": ["2388.TW", "3017.TW", "2395.TW"],
    "資訊服務業": ["2347.TW", "3673.TW", "6214.TW"],
    "綠能環保": ["3576.TW", "6409.TW", "8044.TW"],
}

# 使用者常用的中文別名 → 官方產業別名稱
ALIAS_MAP: dict[str, str] = {
    # 半導體
    "半導體": "半導體業",
    "晶片": "半導體業",
    "IC": "半導體業",
    "ic": "半導體業",
    # 電子
    "電子": "電子零組件業",
    "零組件": "電子零組件業",
    "電腦": "電腦及週邊設備業",
    "代工": "電腦及週邊設備業",
    # 通訊
    "通訊": "通信網路業",
    "電信": "通信網路業",
    "5g": "通信網路業",
    "5G": "通信網路業",
    # 金融
    "金融": "金融保險業",
    "銀行": "金融保險業",
    "保險": "金融保險業",
    "金控": "金融保險業",
    # 傳產 / 原物料
    "傳產": "鋼鐵工業",   # 廣義傳產先指鋼鐵，查詢時會額外加塑膠/化學
    "石化": "化學工業",
    "石油": "油電燃氣業",
    "電力": "油電燃氣業",
    "油電": "油電燃氣業",
    "天然氣": "油電燃氣業",
    "鋼鐵": "鋼鐵工業",
    "塑膠": "塑膠工業",
    "化學": "化學工業",
    # 生技醫療
    "生技": "生技醫療業",
    "醫療": "生技醫療業",
    "生醫": "生技醫療業",
    # 食品
    "食品": "食品工業",
    "食": "食品工業",
    # 航運
    "航運": "航運業",
    "海運": "航運業",
    "航空": "航運業",
    # 建築
    "建設": "建材營造業",
    "營造": "建材營造業",
    "建材": "建材營造業",
    "房地產": "建材營造業",
    # 其他
    "光電": "光電業",
    "太陽能": "綠能環保",
    "綠能": "綠能環保",
    "軟體": "資訊服務業",
    "資訊": "資訊服務業",
}

# 「傳產」是多產業的組合，特別處理
MULTI_SECTOR_ALIAS: dict[str, list[str]] = {
    "傳產": ["鋼鐵工業", "塑膠工業", "化學工業", "紡織纖維", "水泥工業"],
}

SOURCE_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"

# 快取：避免每次都抓 TWSE（每日更新即可，用模組級快取）
_isin_cache: dict[str, list[str]] | None = None


async def _fetch_twse_isin() -> dict[str, list[str]]:
    """抓取 TWSE ISIN 清單，回傳 {產業別: [代號.TW, ...]}。"""
    global _isin_cache
    if _isin_cache is not None:
        return _isin_cache

    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True) as client:
        try:
            resp = await client.get(SOURCE_URL)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception as exc:
            logger.warning(f"TWSE ISIN fetch failed: {exc}")
            return {}

    result: dict[str, list[str]] = {}
    for row in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cells) < 5:
            continue
        raw_code = cells[0].split("　")[0].strip()  # "1101　台泥" → "1101"
        industry = cells[4].strip()
        if not raw_code.isdigit() or not industry or industry == "產業別":
            continue
        result.setdefault(industry, []).append(f"{raw_code}.TW")

    _isin_cache = result
    logger.info(f"TWSE ISIN loaded: {len(result)} industries, {sum(len(v) for v in result.values())} stocks")
    return result


async def get_sector_symbols(
    sector_query: str,
    max_symbols: int = 10,
) -> dict[str, Any]:
    """
    根據產業關鍵字，回傳該類股的代號清單。

    Args:
        sector_query: 使用者輸入，如 "半導體", "石油電力", "傳產"
        max_symbols: 最多回傳幾檔（避免一次分析太多）

    Returns:
        {
          "sector_names": ["半導體業"],
          "symbols": ["2330.TW", ...],
          "total_in_sector": 30,
          "source": "twse_isin" | "fallback",
          "source_url": "...",
        }
    """
    query_lower = sector_query.lower()

    # 解析要查哪些官方產業別
    target_sectors: list[str] = []

    # 多產業別名（傳產 = 鋼鐵+塑膠+化學...）
    for alias, sectors in MULTI_SECTOR_ALIAS.items():
        if alias in sector_query:
            target_sectors.extend(sectors)

    # 單一別名
    for alias, official in ALIAS_MAP.items():
        if alias.lower() in query_lower and official not in target_sectors:
            target_sectors.append(official)

    # 直接輸入官方名（如「半導體業」）
    if not target_sectors:
        target_sectors = [sector_query]

    target_sectors = list(dict.fromkeys(target_sectors))  # dedupe, preserve order

    # ── B 方案：動態抓 TWSE ────────────────────────────────────────────────
    twse_data = await _fetch_twse_isin()
    if twse_data:
        matched_sectors: list[str] = []
        all_symbols: list[str] = []
        total_count = 0
        for ts in target_sectors:
            # 允許部分匹配，如 "半導體業" 或 "半導體"
            for k, v in twse_data.items():
                if ts in k or k in ts:
                    if k not in matched_sectors:
                        matched_sectors.append(k)
                        total_count += len(v)
                        all_symbols.extend(v)

        if matched_sectors:
            # 取前 max_symbols 檔（依代碼排序，小號通常是大市值）
            symbols = sorted(set(all_symbols))[:max_symbols]
            return {
                "sector_names": matched_sectors,
                "symbols": symbols,
                "total_in_sector": total_count,
                "source": "twse_isin",
                "source_url": SOURCE_URL,
            }

    # ── A 方案 fallback ─────────────────────────────────────────────────────
    logger.warning(f"TWSE ISIN unavailable, using fallback for: {target_sectors}")
    fallback_symbols: list[str] = []
    matched_fb: list[str] = []
    for ts in target_sectors:
        for k, v in SECTOR_FALLBACK.items():
            if ts in k or k in ts:
                if k not in matched_fb:
                    matched_fb.append(k)
                    fallback_symbols.extend(v)

    if not matched_fb:
        # 找不到任何匹配
        return {
            "sector_names": [],
            "symbols": [],
            "total_in_sector": 0,
            "source": "fallback",
            "source_url": SOURCE_URL,
            "error": f"找不到對應的產業類別：{sector_query}",
        }

    symbols = list(dict.fromkeys(fallback_symbols))[:max_symbols]
    return {
        "sector_names": matched_fb,
        "symbols": symbols,
        "total_in_sector": len(fallback_symbols),
        "source": "fallback",
        "source_url": SOURCE_URL,
    }


def detect_sector_query(message: str) -> str | None:
    """
    從使用者訊息中偵測是否包含產業關鍵字。
    回傳匹配到的關鍵字，或 None（不是產業查詢）。
    """
    msg_lower = message.lower()
    # 多產業別名優先
    for alias in MULTI_SECTOR_ALIAS:
        if alias in message:
            return alias
    # 單一別名
    for alias in ALIAS_MAP:
        if alias.lower() in msg_lower:
            return alias
    return None
