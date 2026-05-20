"""CMoney 概念股爬蟲：給定主題關鍵字，從 CMoney 概念股分類頁取得相關個股代碼。

流程：
1. 爬 /forum/concept 頁面，建立「概念名稱 → 概念ID」對照表
2. 用關鍵字模糊比對找出最相關的概念 ID
3. 爬 /forum/concept/{ID} 取得個股清單（Nuxt SSR，HTML 中含完整股票連結）
"""

from __future__ import annotations

import asyncio
import re
from difflib import SequenceMatcher
from typing import Any

import httpx
from bs4 import BeautifulSoup
from loguru import logger

CMONEY_BASE = "https://www.cmoney.tw"
CONCEPT_LIST_URL = f"{CMONEY_BASE}/forum/concept"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

# In-memory cache for concept map (keyword → concept_id)
_concept_map: dict[str, str] | None = None
_concept_map_lock = asyncio.Lock()


async def _fetch_concept_map(client: httpx.AsyncClient) -> dict[str, str]:
    """Fetch and parse the CMoney concept list. Returns {concept_name: concept_id}."""
    global _concept_map
    async with _concept_map_lock:
        if _concept_map is not None:
            return _concept_map
        try:
            r = await client.get(CONCEPT_LIST_URL)
            r.raise_for_status()
        except Exception as exc:
            logger.warning(f"CMoney concept list fetch failed: {exc}")
            return {}
        soup = BeautifulSoup(r.text, "lxml")
        mapping: dict[str, str] = {}
        for a in soup.find_all("a", href=re.compile(r"^/forum/concept/C\d+")):
            concept_id = a["href"].split("/")[-1]
            name = a.get_text(strip=True)
            if name:
                mapping[name] = concept_id
        logger.info(f"CMoney concept map loaded: {len(mapping)} concepts")
        _concept_map = mapping
        return mapping


def _best_match(keyword: str, concept_map: dict[str, str]) -> tuple[str, str, float] | None:
    """Fuzzy-match keyword against concept names. Returns (name, id, score) or None."""
    best_score = 0.0
    best_name = ""
    best_id = ""
    kw_lower = keyword.lower()
    for name, cid in concept_map.items():
        # Exact substring check first (fast path)
        if kw_lower in name or name in keyword:
            score = 1.0
        else:
            score = SequenceMatcher(None, kw_lower, name).ratio()
        if score > best_score:
            best_score = score
            best_name = name
            best_id = cid
    if best_score >= 0.4:
        return best_name, best_id, best_score
    return None


async def _fetch_concept_stocks(concept_id: str, client: httpx.AsyncClient) -> list[str]:
    """Fetch stock codes from a CMoney concept page."""
    url = f"{CMONEY_BASE}/forum/concept/{concept_id}"
    try:
        r = await client.get(url)
        r.raise_for_status()
    except Exception as exc:
        logger.warning(f"CMoney concept page {concept_id} fetch failed: {exc}")
        return []
    soup = BeautifulSoup(r.text, "lxml")
    links = soup.find_all("a", href=re.compile(r"^/forum/stock/\d{4}$"))
    codes = list(dict.fromkeys(a["href"].split("/")[-1] for a in links))
    return codes


async def get_concept_stocks(
    keyword: str,
    max_symbols: int = 12,
) -> dict[str, Any]:
    """
    給定主題關鍵字，從 CMoney 概念股頁面取得結構化個股清單。

    Returns:
        {
            "keyword": "機器人",
            "matched_concept": "智慧型機器人/機械手臂",
            "concept_id": "C50050",
            "symbols": ["2049.TW", ...],
            "source": "cmoney_concept",
            "error": None | str,
        }
    """
    async with httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True) as client:
        concept_map = await _fetch_concept_map(client)
        if not concept_map:
            return {"keyword": keyword, "symbols": [], "error": "CMoney concept list unavailable", "source": "cmoney_concept"}

        match = _best_match(keyword, concept_map)
        if not match:
            logger.info(f"CMoney: no concept match for '{keyword}'")
            return {"keyword": keyword, "symbols": [], "matched_concept": None, "concept_id": None, "error": "no match", "source": "cmoney_concept"}

        matched_name, concept_id, score = match
        logger.info(f"CMoney: '{keyword}' → '{matched_name}' ({concept_id}) score={score:.2f}")

        await asyncio.sleep(0.3)
        codes = await _fetch_concept_stocks(concept_id, client)
        symbols = [f"{c}.TW" for c in codes if c != "0050" and c != "0056"][:max_symbols]

    return {
        "keyword": keyword,
        "matched_concept": matched_name,
        "concept_id": concept_id,
        "match_score": round(score, 2),
        "symbols": symbols,
        "source": "cmoney_concept",
        "error": None,
    }
