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

    # LLM CLI backend. Both CLIs use the user's existing local login; no API
    # key is read by this application. Leave codex_model empty to use the
    # model selected by Codex's own configuration.
    llm_backend: Literal["claude", "codex"] = "claude"
    codex_model: str = ""
    codex_reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "medium"
    codex_research_timeout_seconds: int = 300

    # Quote source used by trading execution, stop-losses, conditions, and
    # mark-to-market reporting. Historical/technical data remains on Yahoo.
    market_data_provider: Literal["yahoo"] = "yahoo"

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
    # Codex CLI does not expose USD cost, so call/timeout caps are the
    # backend-independent safety net for unattended decision runs.
    paper_trading_max_llm_calls_per_day: int = 10
    paper_trading_max_timeouts_per_day: int = 3
    paper_trading_failure_cooldown_seconds: int = 3600
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
    # Mechanical stop-loss safety net -- independent of agent judgment, checked
    # every tick (60s) against real fetched prices, not gated by the daily
    # LLM budget (it never calls the LLM). This exists specifically for the
    # failure modes the agent-driven, notes-informed judgment can't cover:
    # a bad read of the chart, or decision calls paused because
    # paper_trading_daily_budget_usd was hit while a position keeps sliding.
    # Not a take-profit -- forcing an exit on gains would cut short the
    # "trailing stop, let winners run" philosophy in the user's own
    # knowledge_base notes. Long-term tolerates a wider drawdown than
    # short-term by design (values are magnitudes; a position is force-sold
    # when pnl_pct <= -this value).
    paper_trading_short_term_stop_loss_pct: float = 15.0
    paper_trading_long_term_stop_loss_pct: float = 20.0
    # Capital simulation -- lets /performance report a realistic equity
    # curve/drawdown alongside (not instead of) the size-agnostic per-trade
    # win_rate/avg_return_pct stats. starting_capital never changes after
    # positions exist (each position's shares/allocation_amount are recorded
    # at entry time, not recomputed from current config). Allocation % per
    # trade is the agent's own call (paper_trade_buy's allocation_pct arg,
    # same "agent decides, mechanical bounds clamp it" shape as horizon
    # isn't clamped but position caps/stop-loss are) -- these bounds exist
    # so one overconfident call can't all-in a single symbol.
    paper_trading_starting_capital: float = 500_000.0
    paper_trading_min_allocation_pct: float = 5.0
    paper_trading_max_allocation_pct: float = 20.0

    # App
    log_level: str = "INFO"
    market: Literal["TW", "US", "HK"] = "TW"
    max_news_per_run: int = 30
    news_lookback_hours: int = 24
    session_ttl_seconds: int = 3600
    news_cache_ttl_seconds: int = 1800  # 30 min news cache


settings = Settings()
