"""Social Signal Agent — PTT Stock board + CMoney forum monitoring."""

import asyncio
from dataclasses import asdict
from loguru import logger

from src.agents.state import AgentState
from src.tools.social_signal import fetch_ptt_stock, filter_signal_posts
from src.tools.cmoney_forum import get_forum_posts


async def social_agent_node(state: AgentState) -> dict:
    logger.info("SocialAgent: fetching PTT + CMoney signals")
    symbols = state.target_symbols

    # PTT always runs
    ptt_task = asyncio.create_task(fetch_ptt_stock(max_pages=2))

    # CMoney forum: one request per target symbol (max 3 to avoid rate limiting)
    cmoney_tasks = []
    if symbols:
        for sym in symbols[:3]:
            cmoney_tasks.append(asyncio.create_task(get_forum_posts(sym, max_posts=8)))

    ptt_posts_raw = await ptt_task
    cmoney_results = await asyncio.gather(*cmoney_tasks, return_exceptions=True)

    # Process PTT
    signal_posts = filter_signal_posts(ptt_posts_raw, min_keywords=1)
    if symbols:
        codes = [s.replace(".TW", "") for s in symbols]
        filtered = [
            p for p in signal_posts
            if any(code in p.title + p.content for code in codes) or
               any(s in p.tickers for s in symbols)
        ]
        if not filtered:
            filtered = signal_posts[:10]
    else:
        filtered = signal_posts[:15]

    post_dicts = [asdict(p) for p in filtered]
    sources = list({p.url for p in filtered if p.url})

    # Process CMoney forum posts — attach to social_signals as extra items
    for result in cmoney_results:
        if isinstance(result, Exception) or not isinstance(result, dict):
            continue
        for post in result.get("posts", []):
            post_dicts.append({
                "source": "CMoney討論區",
                "title": post["title"],
                "content": post.get("content", ""),
                "url": post["url"],
                "keywords": [],
                "tickers": [result["symbol"]],
            })
        if result.get("forum_url"):
            sources.append(result["forum_url"])

    logger.info(f"SocialAgent: {len(filtered)} PTT + {sum(len(r.get('posts',[])) for r in cmoney_results if isinstance(r, dict))} CMoney posts")
    return {"social_signals": post_dicts, "sources": sources}
