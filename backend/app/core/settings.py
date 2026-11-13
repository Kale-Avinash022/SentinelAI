"""Typed application settings, validated at startup — never at import time of routes.

Rules (Day 006):

- Every value comes from the environment (repo-root ``.env`` in development);
  nothing here opens a socket or connection.
- Secrets are never printed: ``SecretStr`` reprs mask their value, and the
  structured logger only ever sees field *names* from ``model_dump``-free code.
- :func:`get_settings` is cached; call it from the app factory / lifespan, not
  at module import, so tests can build several isolated apps.
"""

from __future__ import annotations

import functools
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]


class Settings(BaseSettings):
    """Validated runtime configuration for the FastAPI modular monolith."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = "development"
    debug: bool = False
    log_level: str = "INFO"

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    secret_key: SecretStr = Field(min_length=32)

    # DSN identities from Day 005. The migration URL must never appear here.
    database_url: str = ""
    redis_url: str = ""

    # Bound the request body *before* expensive parsing (see body-size middleware).
    max_request_bytes: int = Field(default=1_048_576, ge=1024)

    async_transport: Literal["celery", "sqs"] = "celery"

    # Readiness probes are short and non-blocking: a slow dependency fails the
    # check instead of hanging the request.
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=10)

    docs_enabled: bool = True

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"log_level must be a valid logging level, got {value!r}")
        return level

    @field_validator("database_url")
    @classmethod
    def _reject_migration_identity(cls, value: str) -> str:
        # Defensive guard: the app runtime must never borrow the DDL role's DSN.
        if "sentinel_migrator" in value:
            raise ValueError(
                "DATABASE_URL must use the application role (sentinel_app); "
                "the migrator identity is reserved for Alembic."
            )
        return value


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings factory. Reads the environment once per process.

    ``model_config.env_file='.env'`` is resolved by pydantic-settings itself.
    The kwargs indirection exists only because required fields are supplied
    by the environment: mypy sees a no-argument call as missing ``secret_key``,
    while runtime validation (including its minimum length) still applies.
    """
    init: dict[str, Any] = {}
    return Settings(**init)
