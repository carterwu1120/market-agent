from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    llm_provider: Literal["ollama", "openai", "gemini", "vertex", "vllm"] = "ollama"
    llm_model: str = "llama3.1:8b"
    ollama_base_url: str = "http://localhost:11434"
    vllm_base_url: str = "http://localhost:8000"
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

    # Scheduler
    schedule_report_channel_id: str = ""
    schedule_timezone: str = "Asia/Taipei"
    schedule_enabled: bool = True
    schedule_user_id: str = "0"

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
