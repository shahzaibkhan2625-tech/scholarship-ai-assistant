from pathlib import Path

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(alias="DATABASE_URL")
    jwt_secret_key: str = Field(alias="JWT_SECRET_KEY")

    # Optional: free-tier external services (Gemini LLM, Qdrant Cloud vectors).
    # Not required so CI (which sets only DATABASE_URL/JWT_SECRET_KEY dummies)
    # can still import the app; code paths that need them raise/skip explicitly
    # when unset, mirroring the DATABASE_URL-unreachable skip pattern in tests.
    qdrant_url: str | None = Field(default=None, alias="QDRANT_URL")
    qdrant_api_key: str | None = Field(default=None, alias="QDRANT_API_KEY")
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")

    @field_validator("qdrant_url", "qdrant_api_key", "gemini_api_key")
    @classmethod
    def _strip_whitespace(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str) -> str:
        # We install psycopg (v3), not psycopg2, so make sure SQLAlchemy
        # picks the right driver even when DATABASE_URL uses the plain
        # "postgresql://" / "postgres://" scheme (e.g. as provided by Neon).
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg://", 1)
        return value


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
