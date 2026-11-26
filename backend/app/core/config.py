from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
_DEFAULT_STORAGE_ROOT = Path(__file__).resolve().parents[2] / "data" / "storage"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        # A validation error must never echo the input: it can hold the signing secret.
        hide_input_in_errors=True,
    )

    database_url: str = Field(alias="DATABASE_URL")
    jwt_secret_key: str = Field(alias="JWT_SECRET_KEY")

    # 010: optional owner/direct URL used only by Alembic (never set in the app's environment).
    migration_database_url: str | None = Field(default=None, alias="MIGRATION_DATABASE_URL")
    # 010: upload cap in bytes (10 MiB) and the redirect-hop limit of the safe fetch.
    max_upload_bytes: int = Field(default=10485760, ge=1, alias="MAX_UPLOAD_BYTES")
    max_fetch_redirects: int = Field(default=5, ge=0, alias="MAX_FETCH_REDIRECTS")

    # Optional: free-tier external services (Gemini LLM, Qdrant Cloud vectors).
    # Not required so CI (which sets only DATABASE_URL/JWT_SECRET_KEY dummies)
    # can still import the app; code paths that need them raise/skip explicitly
    # when unset, mirroring the DATABASE_URL-unreachable skip pattern in tests.
    qdrant_url: str | None = Field(default=None, alias="QDRANT_URL")
    qdrant_api_key: str | None = Field(default=None, alias="QDRANT_API_KEY")
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")

    # Blueprint A3: reranker is OFF by default to control cost/latency; only
    # ever enabled explicitly via this flag.
    rerank_enabled: bool = Field(default=False, alias="RERANK_ENABLED")

    # Local-filesystem object-storage root (T103). Uploaded/generated document
    # bytes live under here; `file_ref` values persisted in the DB are always
    # relative to this root, never an absolute path.
    storage_root: str = Field(default=str(_DEFAULT_STORAGE_ROOT), alias="STORAGE_ROOT")

    # Alert email delivery (all optional; unset disables email alerts).
    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_username: str | None = Field(default=None, alias="SMTP_USERNAME")
    smtp_password: str | None = Field(default=None, alias="SMTP_PASSWORD")
    alerts_from_email: str | None = Field(default=None, alias="ALERTS_FROM_EMAIL")
    # Cap on owner x event re-matches per monitoring run (ADR-0007).
    max_alert_rematches_per_run: int = Field(default=500, ge=1, alias="MAX_ALERT_REMATCHES_PER_RUN")

    # Source monitoring + hardening. monitoring_interval_minutes has no bound:
    # a value <= 0 means the scheduler is disabled.
    monitoring_interval_minutes: int = Field(default=60, alias="MONITORING_INTERVAL_MINUTES")
    fetch_log_retention_days: int = Field(default=90, ge=1, alias="FETCH_LOG_RETENTION_DAYS")
    health_window_n: int = Field(default=5, ge=1, alias="HEALTH_WINDOW_N")
    rate_limit_per_minute: int = Field(default=120, ge=1, alias="RATE_LIMIT_PER_MINUTE")

    # Candidate-source pre-checks (US7, ADR-0010). Consumed by the monitor-run
    # trigger (T244, deferred); defined here so the service and tests share them.
    candidate_validation_recheck_days: int = Field(default=7, ge=1, alias="CANDIDATE_VALIDATION_RECHECK_DAYS")
    max_candidate_validations_per_run: int = Field(default=15, ge=1, alias="MAX_CANDIDATE_VALIDATIONS_PER_RUN")

    # 003 quota governance: every value is optional with a safe default (ADR-0014).
    llm_generation_daily_allowance: int = Field(default=20, ge=1, alias="LLM_GENERATION_DAILY_ALLOWANCE")
    # Unset = counted, not limited.
    llm_embedding_daily_allowance: int | None = Field(default=None, ge=1, alias="LLM_EMBEDDING_DAILY_ALLOWANCE")
    llm_user_facing_reserve: int = Field(default=8, ge=0, alias="LLM_USER_FACING_RESERVE")
    llm_ladder_offset_backfill: int = Field(default=0, ge=0, alias="LLM_LADDER_OFFSET_BACKFILL")
    llm_ladder_offset_monitoring: int = Field(default=2, ge=0, alias="LLM_LADDER_OFFSET_MONITORING")
    llm_ladder_offset_validation: int = Field(default=4, ge=0, alias="LLM_LADDER_OFFSET_VALIDATION")
    llm_backfill_daily_share: int = Field(default=6, ge=0, alias="LLM_BACKFILL_DAILY_SHARE")
    llm_budget_timezone: str = Field(default="America/Los_Angeles", alias="LLM_BUDGET_TIMEZONE")
    llm_default_pause_seconds: int = Field(default=900, ge=1, alias="LLM_DEFAULT_PAUSE_SECONDS")
    # Retry delay for failures that carry no provider time (outage, unparseable reply, not configured).
    llm_unavailable_retry_seconds: int = Field(default=60, ge=1, alias="LLM_UNAVAILABLE_RETRY_SECONDS")
    llm_ledger_retention_days: int = Field(default=90, ge=1, alias="LLM_LEDGER_RETENTION_DAYS")

    cadence_weekly_minutes: int = Field(default=1440, ge=15, alias="CADENCE_WEEKLY_MINUTES")
    cadence_monthly_minutes: int = Field(default=10080, ge=15, alias="CADENCE_MONTHLY_MINUTES")
    cadence_quarterly_minutes: int = Field(default=10080, ge=15, alias="CADENCE_QUARTERLY_MINUTES")
    cadence_annual_minutes: int = Field(default=10080, ge=15, alias="CADENCE_ANNUAL_MINUTES")
    cadence_undeclared_minutes: int = Field(default=1440, ge=15, alias="CADENCE_UNDECLARED_MINUTES")
    forced_full_recheck_days: int = Field(default=7, ge=1, alias="FORCED_FULL_RECHECK_DAYS")
    unextractable_backoff_base_minutes: int = Field(default=60, ge=1, alias="UNEXTRACTABLE_BACKOFF_BASE_MINUTES")
    noise_window_checks: int = Field(default=10, ge=1, alias="NOISE_WINDOW_CHECKS")
    noise_threshold: float = Field(default=0.8, gt=0, le=1, alias="NOISE_THRESHOLD")
    rules_anomaly_low_ratio: float = Field(default=0.5, gt=0, le=1, alias="RULES_ANOMALY_LOW_RATIO")
    rules_anomaly_high_ratio: float = Field(default=2.0, ge=1, alias="RULES_ANOMALY_HIGH_RATIO")

    @field_validator("qdrant_url", "qdrant_api_key", "gemini_api_key")
    @classmethod
    def _strip_whitespace(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("smtp_host", "smtp_username", "alerts_from_email")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("migration_database_url")
    @classmethod
    def _blank_migration_url_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str | None) -> str | None:
        # We install psycopg (v3), not psycopg2, so make sure SQLAlchemy
        # picks the right driver even when DATABASE_URL uses the plain
        # "postgresql://" / "postgres://" scheme (e.g. as provided by Neon).
        if value is None:
            return None
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg://", 1)
        return value

    @field_validator("llm_budget_timezone")
    @classmethod
    def _known_budget_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ValueError("LLM_BUDGET_TIMEZONE must be a known IANA timezone name") from None
        return value

    @model_validator(mode="after")
    def _reserve_below_allowance(self) -> "Settings":
        if self.llm_user_facing_reserve >= self.llm_generation_daily_allowance:
            raise ValueError("LLM_USER_FACING_RESERVE must be below LLM_GENERATION_DAILY_ALLOWANCE")
        return self

    @model_validator(mode="after")
    def _require_strong_signing_secret(self) -> "Settings":
        # The message names the rule only; the value must never appear in it (FR-CRED-3).
        if len(self.jwt_secret_key) < _MIN_JWT_SECRET_CHARS or self.jwt_secret_key == _PLACEHOLDER_JWT_SECRET:
            raise ValueError(
                f"JWT_SECRET_KEY must be at least {_MIN_JWT_SECRET_CHARS} characters long "
                "and must not be the placeholder value; set a long random value"
            )
        return self


_MIN_JWT_SECRET_CHARS = 32
_PLACEHOLDER_JWT_SECRET = "change-me-to-a-random-secret"  # allow-secret


def migration_url(settings: Settings) -> str:
    """The URL Alembic connects with: the owner/direct URL when set, else the app URL."""
    return settings.migration_database_url or settings.database_url


def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        errors = exc.errors()
        # Whole-model rules (the signing-secret check) carry no field name; report the rule text only.
        rules = [error["msg"].removeprefix("Value error, ") for error in errors if not error["loc"]]
        if rules:
            raise RuntimeError(f"Invalid setting: {'; '.join(rules)}.") from None
        missing = ", ".join(str(error["loc"][0]) for error in errors)
        raise RuntimeError(
            f"Missing required setting(s): {missing}. Define them in the "
            f"environment or in {_REPO_ROOT_ENV_FILE} before starting the app."
        ) from exc


settings = get_settings()
