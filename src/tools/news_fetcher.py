"""News fetching tools: RSS, NewsAPI, GNews.

Every NewsArticle carries a source_url so the LLM can cite it explicitly.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import feedparser
import httpx
from loguru import logger

from src.config import settings

# ── RSS feed registry ─────────────────────────────────────────────────────────
TW_RSS_FEEDS = [
    ("鉅亨網-台股", "https://feeds.cnyes.com/feeds/cat/tw_stock.xml"),
    ("鉅亨網-國際", "https://feeds.cnyes.com/feeds/cat/wd_stock.xml"),
    ("MoneyUDN", "https://money.udn.com/rssfeed/news/1001/5590?ch=money"),
    ("經濟日報", "https://money.udn.com/rssfeed/news/1001/5591?ch=money"),
]

INTERNATIONAL_RSS_FEEDS = [
    ("Reuters Business", "https://feeds.reuters.com/reuters/businessNews"),
    ("Reuters Technology", "https://feeds.reuters.com/reuters/technologyNews"),
    ("Bloomberg Markets", "https://feeds.bloomberg.com/markets/news.rss"),
    ("FT Markets", "https://www.ft.com/rss/home/uk"),
]


@dataclass
class NewsArticle:
    title: str
    content: str
    source_url: str
    source_name: str
    published_at: datetime
    tickers: list[str] = field(default_factory=list)


def _parse_feed_entry(entry: feedparser.FeedParserDict, source_name: str) -> NewsArticle | None:
    try:
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        if published:
            pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
        else:
            pub_dt = datetime.now(timezone.utc)

        content = entry.get("summary") or entry.get("description") or ""
        return NewsArticle(
            title=entry.get("title", "").strip(),
            content=content[:2000],
            source_url=entry.get("link", ""),
            source_name=source_name,
            published_at=pub_dt,
        )
    except Exception as exc:
        logger.warning(f"Failed to parse feed entry: {exc}")
        return None


async def fetch_rss_news(lookback_hours: int | None = None) -> list[NewsArticle]:
    lookback_hours = lookback_hours or settings.news_lookback_hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    feeds = TW_RSS_FEEDS + INTERNATIONAL_RSS_FEEDS
    articles: list[NewsArticle] = []

    async with httpx.AsyncClient(timeout=15) as client:
        for name, url in feeds:
            try:
                resp = await client.get(url, follow_redirects=True)
                resp.raise_for_status()
                parsed = feedparser.parse(resp.text)
                for entry in parsed.entries:
                    article = _parse_feed_entry(entry, name)
                    if article and article.published_at >= cutoff:
                        articles.append(article)
            except Exception as exc:
                logger.warning(f"RSS fetch failed [{name}]: {exc}")

    logger.info(f"RSS: fetched {len(articles)} articles")
    return articles


async def fetch_newsapi(query: str = "stock market Taiwan", lookback_hours: int | None = None) -> list[NewsArticle]:
    if not settings.newsapi_key:
        return []

    lookback_hours = lookback_hours or settings.news_lookback_hours
    from_dt = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%S")

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "from": from_dt,
                    "sortBy": "publishedAt",
                    "language": "en",
                    "pageSize": settings.max_news_per_run,
                    "apiKey": settings.newsapi_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning(f"NewsAPI fetch failed: {exc}")
            return []

    articles = []
    for item in data.get("articles", []):
        try:
            pub_dt = datetime.fromisoformat(item["publishedAt"].replace("Z", "+00:00"))
            articles.append(NewsArticle(
                title=item.get("title", ""),
                content=(item.get("description") or "") + "\n" + (item.get("content") or ""),
                source_url=item.get("url", ""),
                source_name=item.get("source", {}).get("name", "NewsAPI"),
                published_at=pub_dt,
            ))
        except Exception:
            continue

    logger.info(f"NewsAPI: fetched {len(articles)} articles for query '{query}'")
    return articles


async def fetch_gnews(query: str = "stock market", lookback_hours: int | None = None) -> list[NewsArticle]:
    """Fetch news from GNews API. Free tier has 12h delay."""
    if not settings.gnews_api_key:
        return []

    lookback_hours = lookback_hours or settings.news_lookback_hours
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(
                "https://gnews.io/api/v4/search",
                params={
                    "q": query,
                    "max": settings.max_news_per_run,
                    "apikey": settings.gnews_api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning(f"GNews fetch failed: {exc}")
            return []

    articles = []
    for item in data.get("articles", []):
        try:
            pub_dt = datetime.fromisoformat(item["publishedAt"].replace("Z", "+00:00"))
            articles.append(NewsArticle(
                title=item.get("title", ""),
                content=(item.get("description") or "") + "\n" + (item.get("content") or ""),
                source_url=item.get("url", ""),
                source_name=item.get("source", {}).get("name", "GNews"),
                published_at=pub_dt,
            ))
        except Exception:
            continue

    logger.info(f"GNews: fetched {len(articles)} articles for query '{query}'")
    return articles


async def fetch_all_news(lookback_hours: int | None = None) -> list[NewsArticle]:
    """Aggregate from all sources concurrently, deduplicate by URL.

    Each source fails independently — one failure does not affect others.
    """
    import asyncio
    results = await asyncio.gather(
        fetch_rss_news(lookback_hours),
        fetch_newsapi(lookback_hours=lookback_hours),
        fetch_gnews(lookback_hours=lookback_hours),
        return_exceptions=True,
    )

    all_articles = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"News source failed: {r}")
        else:
            all_articles.extend(r)

    # Deduplicate by URL
    seen: set[str] = set()
    unique = []
    for a in all_articles:
        if a.source_url and a.source_url not in seen:
            seen.add(a.source_url)
            unique.append(a)

    logger.info(f"Total unique articles: {len(unique)}")
    return unique
