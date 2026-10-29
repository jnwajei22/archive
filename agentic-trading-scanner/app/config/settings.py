import json
from datetime import time
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_mode: str = "production"
    app_env: str = "development"
    app_timezone: str = "America/Chicago"
    public_base_url: str = "https://mcp.justinnwajei.com"
    frontend_origin: str = "https://app.agenticforexdesk.com"
    chatgpt_return_url: str = "https://chatgpt.com"
    chatgpt_setup_url: str | None = None
    oauth_authorization_url: str | None = None
    oauth_token_url: str | None = None
    oauth_transaction_secret: str | None = None
    oauth_allowed_client_ids: str | None = None
    oauth_access_token_ttl_seconds: int = Field(default=3600, gt=0)
    oauth_refresh_token_ttl_seconds: int = Field(default=7776000, gt=0)
    onboarding_assertion_secret: str | None = None
    onboarding_assertion_issuers: str | None = None
    sqlite_path: str = "storage/app.db"
    broker_secret_key: str | None = None
    broker_secret_keys_json: str = "{}"
    active_encryption_key_version: str = "v1"
    allow_env_broker_fallback: bool = False
    local_user_id: str = "local-autonomous-owner"
    local_mcp_api_token: str | None = Field(default=None, repr=False)
    allow_unauthenticated_public_mcp: bool = False
    allow_remote_account_switching: bool = False
    allow_remote_credential_changes: bool = False
    allow_remote_strategy_changes: bool = False
    allow_remote_risk_changes: bool = False

    database_url: str | None = None
    tradingview_webhook_secret: str | None = None
    mcp_shared_secret: str | None = None
    mcp_allow_public_no_auth: bool = False
    mcp_require_oauth: bool = True
    auth_issuer: str | None = None
    auth_audience: str | None = None
    auth_jwks_url: str | None = None

    live_trading_enabled: bool = False
    kill_switch_enabled: bool = True
    market_data_provider: str = "mock"

    default_max_risk_percent: float = 0.5
    default_max_daily_loss_percent: float = 2.0
    default_max_weekly_loss_percent: float = 5.0
    default_min_reward_risk: float = 1.5

    tradelocker_environment: str = "demo"
    tradelocker_base_url: str = "https://demo.tradelocker.com/backend-api"
    tradelocker_demo_base_url: str = "https://demo.tradelocker.com/backend-api"
    tradelocker_username: str | None = None
    tradelocker_email: str | None = None
    tradelocker_password: str | None = None
    tradelocker_server: str | None = None
    tradelocker_account_id: str | None = None
    tradelocker_account_number: str | None = None
    tradelocker_config_cache_ttl_seconds: int = 900
    tradelocker_candle_cache_grace_seconds: int = Field(default=30, ge=0, le=300)
    tradelocker_candle_cache_max_stale_seconds: int = Field(default=86400, ge=0)
    tradelocker_candle_cache_max_entries: int = Field(default=1000, ge=10)
    tradelocker_rate_limit_max_retries: int = Field(default=2, ge=0, le=5)
    tradelocker_rate_limit_max_backoff_seconds: float = Field(default=8.0, ge=0.1, le=60)
    tradelocker_rate_limit_min_interval_seconds: float = Field(default=0.35, ge=0, le=10)

    autonomous_snapshot_ttl_seconds: int = 60
    autonomous_preview_ttl_seconds: int = 60
    autonomous_quote_max_age_seconds: int = 30
    autonomous_price_tolerance_percent: float = 0.25
    autonomous_max_spread_pips: float = 3.0
    autonomous_broker_verification_max_attempts: int = Field(default=6, ge=1, le=20)
    autonomous_broker_verification_timeout_seconds: float = Field(default=12.0, ge=1, le=30)
    autonomous_broker_verification_initial_delay_seconds: float = Field(default=0.75, ge=0, le=5)
    autonomous_broker_verification_max_delay_seconds: float = Field(default=2.0, ge=0, le=10)
    autonomous_news_blackout_minutes: int = 30
    autonomous_decision_provider: str = "no_trade"
    autonomous_decision_model: str = "gpt-5.6"
    autonomous_decision_timeout_seconds: float = 30
    autonomous_decision_max_retries: int = 1
    autonomous_decision_max_input_chars: int = 80000
    autonomous_default_minimum_confidence: float = 0.70
    autonomous_minimum_confidence: float = Field(default=0.80, ge=0, le=1)
    autonomous_minimum_risk_reward: float = Field(default=2.0, gt=0)
    autonomous_risk_per_trade_percent: float = Field(default=0.25, gt=0, le=1)
    autonomous_max_total_risk_percent: float = Field(default=1.0, gt=0, le=10)
    autonomous_max_daily_loss_percent: float = Field(default=1.5, gt=0, le=3)
    autonomous_max_drawdown_percent: float = Field(default=5.0, gt=0, le=10)
    autonomous_max_open_positions: int = Field(default=3, ge=1, le=100)
    autonomous_max_open_positions_per_symbol: int = Field(default=1, ge=1, le=20)
    autonomous_max_trades_per_day: int = Field(default=5, ge=1, le=100)
    autonomous_cooldown_minutes: int = Field(default=30, ge=0, le=10080)
    autonomous_require_stop_loss: bool = True
    autonomous_require_take_profit: bool = True
    autonomous_allow_overnight: bool = False
    autonomous_allow_weekend_crypto: bool = False
    autonomous_strategy_name: str = "whole_market_multi_timeframe_v1"
    autonomous_strategy_prompt: str = ""
    autonomous_strategy_json: str = "{}"
    autonomous_allowed_asset_classes: str = "forex,metals,energies,indices,crypto"
    autonomous_allowed_symbols: str = ""
    autonomous_blocked_symbols: str = ""
    autonomous_allowed_directions: str = "long,short"
    autonomous_scan_timeframes: str = "5m,15m,1h,4h,1d"
    autonomous_confirmation_timeframes: str = "1h,4h,1d"
    autonomous_maximum_instruments_screened: int = Field(default=100,ge=1,le=1000)
    autonomous_maximum_instruments_deeply_analyzed: int = Field(default=5,ge=1,le=50)
    autonomous_maximum_upstream_requests_per_run: int = Field(default=40,ge=1,le=500)
    autonomous_maximum_run_duration_seconds: float = Field(default=45,gt=0,le=300)
    autonomous_max_history_requests_per_cycle: int = Field(default=20, ge=1, le=500)
    autonomous_htf_candidate_limit: int = Field(default=20, ge=1, le=100)
    autonomous_ltf_candidate_limit: int = Field(default=8, ge=1, le=50)
    autonomous_entry_candidate_limit: int = Field(default=3, ge=1, le=20)
    # Deprecated compatibility setting. Timed process arming is no longer enforced.
    autonomous_max_arming_hours: int = Field(default=0, ge=0, le=168)
    openai_api_key: str | None = Field(default=None, repr=False)
    # Durable execution cadence. Fifteen minutes aligns scheduled decisions with the
    # shortest default candle while preserving the existing per-run and daily risk gates.
    autonomous_schedule_interval_minutes: int = Field(default=15, ge=15, le=1440)
    autonomous_schedule_start_time: str = "00:00"
    autonomous_schedule_end_time: str = "23:45"
    autonomous_schedule_days: str = "monday,tuesday,wednesday,thursday,friday,saturday,sunday"
    autonomous_schedule_market_aware: bool = True
    autonomous_scheduler_poll_seconds: float = Field(default=30,gt=0)
    autonomous_scheduler_lease_seconds: int = Field(default=180,ge=30)
    autonomous_scheduler_batch_size: int = Field(default=20,ge=1,le=100)
    autonomous_scheduler_max_retries: int = Field(default=2,ge=0,le=5)
    autonomous_scheduler_retry_base_seconds: int = Field(default=30,ge=1)
    autonomous_scheduler_retry_cap_seconds: int = Field(default=300,ge=1)
    autonomous_scheduler_heartbeat_stale_seconds: int = Field(default=120,ge=30)
    autonomous_scheduler_required_for_readiness: bool = False
    autonomous_scheduler_embedded: bool = False
    autonomous_execution_enabled: bool = False
    autonomous_position_management_enabled: bool = True
    autonomous_close_enabled: bool = True
    autonomous_partial_close_enabled: bool = True
    autonomous_stop_modification_enabled: bool = True
    autonomous_take_profit_modification_enabled: bool = True
    autonomous_break_even_enabled: bool = True
    autonomous_trailing_stop_enabled: bool = False
    autonomous_cancel_pending_enabled: bool = True
    autonomous_emergency_exit_enabled: bool = True
    autonomous_break_even_buffer_pips: float = Field(default=1.0, ge=0, le=100)
    autonomous_trailing_distance_atr_multiplier: float = Field(default=1.5, gt=0, le=20)
    autonomous_trailing_min_step_pips: float = Field(default=5.0, gt=0, le=1000)
    autonomous_management_decision_max_age_seconds: int = Field(default=60, ge=5, le=3600)
    autonomous_management_lease_seconds: int = Field(default=120, ge=15, le=1800)

    finnhub_enabled: bool = False
    finnhub_api_key: str | None = None
    finnhub_base_url: str = "https://finnhub.io/api/v1"
    finnhub_timeout_seconds: float = 15
    finnhub_max_retries: int = 2
    finnhub_cache_ttl_seconds: int = 300

    fred_enabled: bool = False
    fred_api_key: str | None = None
    fred_base_url: str = "https://api.stlouisfed.org/fred"
    fred_timeout_seconds: float = 15
    fred_max_retries: int = 2
    fred_cache_ttl_seconds: int = 3600
    macro_catalog_json: str = "{}"

    market_data_default_candles: int = 300
    market_data_max_response_candles: int = 2000
    market_data_max_retrieval_candles: int = 10000
    market_data_max_pages: int = 50
    market_series_cache_ttl_seconds: int = 600
    market_series_cache_max_items: int = 100

    @property
    def is_local_autonomous(self) -> bool:
        return self.app_mode.strip().lower() == "local_autonomous"

    @staticmethod
    def _csv(value: str) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))

    @property
    def strategy_config(self) -> dict:
        return json.loads(self.autonomous_strategy_json or "{}")

    @property
    def allowed_asset_classes(self) -> list[str]: return self._csv(self.autonomous_allowed_asset_classes.lower())
    @property
    def allowed_symbols(self) -> list[str]: return self._csv(self.autonomous_allowed_symbols.upper())
    @property
    def blocked_symbols(self) -> list[str]: return self._csv(self.autonomous_blocked_symbols.upper())
    @property
    def allowed_directions(self) -> list[str]: return self._csv(self.autonomous_allowed_directions.lower())
    @property
    def scan_timeframes(self) -> list[str]: return self._csv(self.autonomous_scan_timeframes.lower())
    @property
    def confirmation_timeframes(self) -> list[str]: return self._csv(self.autonomous_confirmation_timeframes.lower())
    @property
    def schedule_days(self) -> list[str]: return self._csv(self.autonomous_schedule_days.lower())

    @model_validator(mode="after")
    def validate_local_contract(self) -> "Settings":
        try:
            parsed_strategy = json.loads(self.autonomous_strategy_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("AUTONOMOUS_STRATEGY_JSON must contain valid JSON.") from exc
        if not isinstance(parsed_strategy, dict):
            raise ValueError("AUTONOMOUS_STRATEGY_JSON must be a JSON object.")
        if not (self.autonomous_entry_candidate_limit <= self.autonomous_ltf_candidate_limit
                <= self.autonomous_htf_candidate_limit):
            raise ValueError(
                "Autonomous candidate limits must satisfy entry <= LTF <= HTF."
            )
        try:
            start = time.fromisoformat(self.autonomous_schedule_start_time)
            end = time.fromisoformat(self.autonomous_schedule_end_time)
        except ValueError as exc:
            raise ValueError("AUTONOMOUS_SCHEDULE_START_TIME and AUTONOMOUS_SCHEDULE_END_TIME must use HH:MM.") from exc
        if end < start:
            raise ValueError("AUTONOMOUS_SCHEDULE_END_TIME must not be earlier than AUTONOMOUS_SCHEDULE_START_TIME.")
        valid_days = {"monday","tuesday","wednesday","thursday","friday","saturday","sunday"}
        if not self.schedule_days or set(self.schedule_days) - valid_days:
            raise ValueError("AUTONOMOUS_SCHEDULE_DAYS contains an unsupported day.")
        start_minutes = start.hour * 60 + start.minute
        end_minutes = end.hour * 60 + end.minute
        if start_minutes % self.autonomous_schedule_interval_minutes != 0:
            raise ValueError("AUTONOMOUS_SCHEDULE_START_TIME must align to AUTONOMOUS_SCHEDULE_INTERVAL_MINUTES.")
        if end_minutes % self.autonomous_schedule_interval_minutes != 0:
            raise ValueError("AUTONOMOUS_SCHEDULE_END_TIME must align to AUTONOMOUS_SCHEDULE_INTERVAL_MINUTES.")
        if self.database_url and self.database_url.startswith("sqlite:///"):
            prefix = "sqlite:///"
            self.sqlite_path = self.database_url[len(prefix):]
        if self.tradelocker_email and not self.tradelocker_username:
            self.tradelocker_username = self.tradelocker_email
        if not self.is_local_autonomous:
            return self
        if self.database_url and not self.database_url.startswith("sqlite:///"):
            raise ValueError("Local autonomous mode requires a sqlite:/// DATABASE_URL.")
        self.mcp_require_oauth = False
        missing = [name for name, value in (
            ("LOCAL_USER_ID", self.local_user_id),
            ("BROKER_SECRET_KEY", self.broker_secret_key),
            ("TRADELOCKER_EMAIL", self.tradelocker_username),
            ("TRADELOCKER_PASSWORD", self.tradelocker_password),
            ("TRADELOCKER_SERVER", self.tradelocker_server),
            ("TRADELOCKER_ACCOUNT_ID", self.tradelocker_account_id),
        ) if not str(value or "").strip()]
        if missing:
            raise ValueError("Local autonomous configuration is incomplete: " + ", ".join(missing))
        parsed = urlsplit(self.tradelocker_base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("TRADELOCKER_BASE_URL must be a credential-free HTTPS URL.")
        if self.tradelocker_environment == "demo":
            self.tradelocker_demo_base_url = self.tradelocker_base_url
        self.tradelocker_server = (self.tradelocker_server or "").strip()
        public = urlsplit(self.public_base_url)
        hostname = (public.hostname or "").lower()
        public_host = hostname not in {"", "localhost", "127.0.0.1", "::1"}
        if public_host and public.scheme != "https":
            raise ValueError("PUBLIC_BASE_URL must use HTTPS when it is public.")
        if public_host and not (self.local_mcp_api_token or "").strip() and not self.allow_unauthenticated_public_mcp:
            raise ValueError("LOCAL_MCP_API_TOKEN is required when PUBLIC_BASE_URL is public.")
        if self.autonomous_risk_per_trade_percent > self.autonomous_max_total_risk_percent:
            raise ValueError("AUTONOMOUS_RISK_PER_TRADE_PERCENT cannot exceed AUTONOMOUS_MAX_TOTAL_RISK_PERCENT.")
        if not self.allowed_directions or set(self.allowed_directions) - {"long", "short"}:
            raise ValueError("AUTONOMOUS_ALLOWED_DIRECTIONS may contain only long and short.")
        if not self.scan_timeframes:
            raise ValueError("AUTONOMOUS_SCAN_TIMEFRAMES must not be empty.")
        allowed_classes={"forex","metals","energies","indices","crypto","equities"}
        if not self.allowed_asset_classes or set(self.allowed_asset_classes)-allowed_classes:
            raise ValueError("AUTONOMOUS_ALLOWED_ASSET_CLASSES contains an unsupported class.")
        if self.autonomous_decision_provider != "openai":
            raise ValueError("Local autonomous mode requires AUTONOMOUS_DECISION_PROVIDER=openai.")
        Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        return self

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
