"""Social Signal Agent — PTT Stock board monitoring."""

from dataclasses import asdict
from loguru import logger

from src.agents.state import AgentState
from src.tools.social_signal import fetch_ptt_stock, filter_signal_posts


async def social_agent_node(state: AgentState) -> dict:
    logger.info("SocialAgent: fetching PTT signals")
    posts = await fetch_ptt_stock(max_pages=2)
    signal_posts = filter_signal_posts(posts, min_keywords=1)

    # If we have target symbols, further filter
    symbols = state.target_symbols
    if symbols:
        codes = [s.replace(".TW", "") for s in symbols]
        filtered = [
            p for p in signal_posts
            if any(code in p.title + p.content for code in codes) or
               any(s in p.tickers for s in symbols)
        ]
        if not filtered:
            filtered = signal_posts[:10]  # fallback to general signals
    else:
        filtered = signal_posts[:15]

    post_dicts = [asdict(p) for p in filtered]
    sources = list({p.url for p in filtered if p.url})

    logger.info(f"SocialAgent: returning {len(post_dicts)} signal posts")
    return {"social_signals": post_dicts, "sources": sources}
