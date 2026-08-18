from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM backend
    # "claude_code": drive `claude -p` (Claude Code CLI) — billed against the
    #   Claude Code subscription, no metered API key needed. Default.
    # "openai" | "gemini" | "vertex": LiteLLM against a cloud API key, kept as
    #   a fallback for anyone who wants a metered-key setup instead.
    llm_backend: Literal["claude_code", "openai", "gemini", "vertex"] = "claude_code"
    llm_provider: Literal["openai", "gemini", "vertex"] = "openai"  # only used when llm_backend != claude_code
    llm_model: str = "gpt-4o-mini"
    openai_api_key: str = ""
    gemini_api_key: str = ""
    google_cloud_project: str = ""
    google_cloud_location: str = "us-central1"

    # Embedding
    embedding_provider: Literal["local", "openai"] = "local"
    embedding_model: str = "BAAI/bge-m3"

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

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "market_agent"
    postgres_user: str = "market_agent"
    postgres_password: str = "changeme"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

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

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_dsn_sync(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
