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

    # App
    log_level: str = "INFO"
    market: Literal["TW", "US", "HK"] = "TW"
    max_news_per_run: int = 30
    news_lookback_hours: int = 24
    session_ttl_seconds: int = 3600
    news_cache_ttl_seconds: int = 1800  # 30 min news cache


settings = Settings()
