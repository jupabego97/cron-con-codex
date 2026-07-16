from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from the environment, never from source code."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Retail Intelligence Platform"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_log_level: str = "INFO"
    app_secret_key: SecretStr | None = None

    database_url: str | None = None
    redis_url: str | None = None
    alegra_api_basic_token: SecretStr | None = None
    alegra_webhook_secret: SecretStr | None = None

    @model_validator(mode="after")
    def require_production_secrets(self) -> "Settings":
        if self.app_env == "production" and self.app_secret_key is None:
            raise ValueError("APP_SECRET_KEY is required in production")
        return self


def normalize_database_url(database_url: str) -> str:
    """Select the psycopg v3 SQLAlchemy dialect for Railway/Postgres URLs."""
    for prefix in ("postgres://", "postgresql://", "postgresql+psycopg2://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix) :]
    return database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
