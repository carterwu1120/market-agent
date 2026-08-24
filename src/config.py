from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Discord
    discord_bot_token: str = ""
    discord_guild_id: str = ""
    discord_owner_user_id: str = ""  # your Discord user ID for agent DMs

    # Scheduler
    schedule_report_channel_id: str = ""
    schedule_timezone: str = "Asia/Taipei"
    schedule_enabled: bool = True
    schedule_user_id: str = "0"

    # Channel allowlist — comma-separated channel IDs bot will respond in
    # If empty, bot responds everywhere
    allowed_channel_ids: str = ""

    @property
    def allowed_channels(self) -> set[str]:
        if not self.allowed_channel_ids:
            return set()
        return {c.strip() for c in self.allowed_channel_ids.split(",") if c.strip()}

    # Local storage (SQLite — replaces Postgres + Redis)
    db_path: str = "data/market_agent.db"

    # News
    newsapi_key: str = ""
    gnews_api_key: str = ""

    # Gmail OAuth
    gmail_credentials_file: str = "gmail_credentials.json"
    gmail_token_file: str = "gmail_token.json"

    # Paper trading loop (experimental) — runs as a background task inside
    # the same process as the Discord bot, not a separate program
    paper_trading_enabled: bool = False
    # Hard spend cap per trading day (USD, based on claude -p's own reported
    # total_cost_usd) -- run_research() calls run agentically and can cost
    # $0.40+ per cycle, unattended, for hours. Once exceeded, decision calls
    # pause until the next trading session; candidate discovery (cheap,
    # non-agentic) keeps running.
    paper_trading_daily_budget_usd: float = 5.0
    # Concentration limits -- without these, paper_trade_buy has no ceiling
    # and the agent could keep opening positions indefinitely. Deliberately
    # set high (not a tight operational cap) -- paper trading risks no real
    # capital, so this exists to catch runaway/bug-driven buying, not to
    # second-guess the agent's judgment in a genuinely strong market. Same
    # "hard rule as a backstop, not the primary constraint" shape as
    # WATCHLIST_TTL. Tracked separately per horizon (not a combined total)
    # so short-term churn can't crowd out long-term holds' allotment.
    paper_trading_max_short_term_positions: int = 20
    paper_trading_max_long_term_positions: int = 20

    # App
    log_level: str = "INFO"
    market: Literal["TW", "US", "HK"] = "TW"
    max_news_per_run: int = 30
    news_lookback_hours: int = 24
    session_ttl_seconds: int = 3600
    news_cache_ttl_seconds: int = 1800  # 30 min news cache


settings = Settings()
