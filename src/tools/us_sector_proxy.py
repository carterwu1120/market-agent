"""Maps a Taiwan stock's TWSE official industry to a comparable US market
proxy (ETF/index/commodity future), so daily_brief can show what happened
overnight in the analogous US market segment -- not just broad indices
(S&P500/NASDAQ/Dow via get_market_indices), which say nothing useful when
today's hot stocks are e.g. shipping or petrochemical names.

Deliberately NOT exhaustive: TWSE has ~30 official industry categories, and
several genuinely have no clean single US analog. Forcing a weak proxy for
those would violate the same "never fabricate a relationship" principle
this project applies to data itself -- industries with no confident proxy
here are just skipped, not guessed.
"""

from __future__ import annotations

import asyncio

import yfinance as yf
from loguru import logger

from src.tools.sector_data import _fetch_twse_isin

# TWSE 官方產業別 -> (yfinance ticker, 顯示名稱)
INDUSTRY_TO_PROXY: dict[str, tuple[str, str]] = {
    "半導體業": ("^SOX", "費城半導體指數"),
    "電子零組件業": ("QQQ", "Nasdaq 100 ETF"),
    "電腦及週邊設備業": ("AAPL", "Apple（代工鏈需求指標）"),
    "通信網路業": ("QQQ", "Nasdaq 100 ETF"),
    "金融保險業": ("XLF", "美股金融類股 ETF"),
    "油電燃氣業": ("CL=F", "WTI 原油期貨"),
    "塑膠工業": ("CL=F", "WTI 原油期貨（塑化原料連動）"),
    "鋼鐵工業": ("SLX", "美股鋼鐵類股 ETF"),
    "化學工業": ("XLB", "美股原物料類股 ETF"),
    "生技醫療業": ("XBI", "美股生技類股 ETF"),
    "食品工業": ("XLP", "美股必需消費類股 ETF"),
    "航運業": ("BDRY", "散裝航運運價 ETF"),
    "建材營造業": ("ITB", "美國住宅營建類股 ETF"),
    "電機機械": ("XLI", "美股工業類股 ETF"),
    "光電業": ("^SOX", "費城半導體指數"),
    "其他電子業": ("QQQ", "Nasdaq 100 ETF"),
    "資訊服務業": ("IGV", "美股軟體類股 ETF"),
    "綠能環保": ("ICLN", "全球潔淨能源 ETF"),
}


async def get_symbol_industries(symbols: list[str]) -> dict[str, str]:
    """{symbol: 官方產業別}。查不到的 symbol 不會出現在結果裡。"""
    isin_data = await _fetch_twse_isin()
    if not isin_data:
        return {}
    code_to_industry = {code: industry for industry, codes in isin_data.items() for code in codes}
    return {s: code_to_industry[s] for s in symbols if s in code_to_industry}


async def get_us_sector_proxies(symbols: list[str]) -> dict[str, dict]:
    """依今日熱門股所屬產業，回傳對應的美股/大宗商品參考資料。

    Returns {顯示名稱: {"close","change_pct","date","source","industries","ticker"}},
    只包含 INDUSTRY_TO_PROXY 裡有對應、且今日熱門股實際命中的產業（多支同產業股票
    共用同一次抓取，不重複查詢）。
    """
    industries = await get_symbol_industries(symbols)
    if not industries:
        return {}

    industry_symbols: dict[str, list[str]] = {}
    for sym, industry in industries.items():
        industry_symbols.setdefault(industry, []).append(sym)

    # ticker -> (display_name, [產業名稱, ...])
    ticker_map: dict[str, tuple[str, list[str]]] = {}
    for industry in industry_symbols:
        proxy = INDUSTRY_TO_PROXY.get(industry)
        if not proxy:
            continue
        ticker, name = proxy
        entry = ticker_map.setdefault(ticker, (name, []))
        entry[1].append(industry)

    if not ticker_map:
        return {}

    def _fetch(ticker: str) -> dict:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="2d")
            if len(hist) < 2:
                return {"error": "no data"}
            last, prev = hist.iloc[-1], hist.iloc[-2]
            change_pct = round(
                (float(last["Close"]) - float(prev["Close"])) / float(prev["Close"]) * 100, 2
            )
            return {
                "close": round(float(last["Close"]), 2),
                "change_pct": change_pct,
                "date": hist.index[-1].strftime("%Y-%m-%d"),
                "source": f"https://finance.yahoo.com/quote/{ticker}/",
            }
        except Exception as exc:
            logger.warning(f"us_sector_proxy fetch failed [{ticker}]: {exc}")
            return {"error": str(exc)}

    tickers = list(ticker_map.keys())
    results = await asyncio.gather(*[asyncio.to_thread(_fetch, t) for t in tickers])

    output: dict[str, dict] = {}
    for ticker, data in zip(tickers, results):
        name, industries_hit = ticker_map[ticker]
        output[name] = {**data, "industries": industries_hit, "ticker": ticker}
    return output
