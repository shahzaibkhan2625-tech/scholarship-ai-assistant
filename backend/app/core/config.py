from pathlib import Path

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
_DEFAULT_STORAGE_ROOT = Path(__file__).resolve().parents[2] / "data" / "storage"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
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


def migration_url(settings: Settings) -> str:
    """The URL Alembic connects with: the owner/direct URL when set, else the app URL."""
    return settings.migration_database_url or settings.database_url


def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        missing = ", ".join(str(error["loc"][0]) for error in exc.errors())
        raise RuntimeError(
            f"Missing required setting(s): {missing}. Define them in the "
            f"environment or in {_REPO_ROOT_ENV_FILE} before starting the app."
        ) from exc


settings = get_settings()
