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

    # --- Authentication / sessions (Day 012) --------------------------------
    # The HMAC signing key is derived from ``secret_key``; only a single
    # algorithm is ever accepted so a token can not downgrade to "none".
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_issuer: str = "sentinelai-api"
    jwt_audience: str = "sentinelai-dashboard"
    # Access tokens are short-lived (memory-only on the client); refresh
    # sessions are longer-lived, stored hashed, and rotated on every use.
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_token_ttl_seconds: int = Field(default=1_209_600, ge=3600, le=2_592_000)
    # Cookie hardening. ``cookie_secure`` is tri-state: ``None`` derives the
    # value from ``environment`` (Secure on in production, off for local HTTP);
    # an explicit ``True``/``False`` overrides that. Refresh always uses
    # HttpOnly + SameSite=lax and is gated by an Origin/CSRF check server-side.
    cookie_secure: bool | None = None
    refresh_cookie_name: str = "sentinel_refresh"
    csrf_cookie_name: str = "sentinel_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    # Comma-separated exact origins permitted to send credentialed refresh
    # requests (CSRF/origin defence). Loopback dev origins by default.
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Simple fixed-window login throttle (per client+email) to blunt
    # enumeration/brute force. Count/seconds are conservative defaults.
    login_max_attempts: int = Field(default=8, ge=1, le=100)
    login_window_seconds: int = Field(default=300, ge=30, le=3600)

    # --- API keys (Day 013) --------------------------------------------------
    # Rotation overlap: a rotated predecessor stays valid for at most this
    # many seconds (and never past its own expiry), giving callers a deliberate
    # cutover window. 0 collapses the overlap — revocation semantics then apply
    # immediately on rotation too.
    api_key_rotation_grace_seconds: int = Field(default=3600, ge=0, le=86_400)

    # --- LLM gateway (Day 015) ----------------------------------------------
    # The chat deadline the gateway enforces regardless of what an adapter
    # would eventually return. 15s matches the brief's synchronous critical path.
    llm_default_timeout_ms: int = Field(default=15_000, ge=250, le=60_000)
    # Hard ceiling on a caller-supplied per-request timeout, so a client cannot
    # pin a worker open far beyond the operating point.
    llm_max_timeout_ms: int = Field(default=30_000, ge=250, le=120_000)
    # Upper bound on a request's requested output tokens; the fake ignores the
    # value but the contract carries it for real adapters and budgets.
    llm_max_output_tokens: int = Field(default=4_096, ge=1, le=32_000)
    # Which deterministic fake behaviour ``sentinel-auto`` resolves to. Only
    # honoured outside production; a validated member of FakeScenario.
    llm_fake_scenario: str = "success"

    @field_validator("llm_fake_scenario")
    @classmethod
    def _validate_fake_scenario(cls, value: str) -> str:
        # Local import keeps core (no domain deps) clean while still pinning
        # the value to the closed scenario enum the registry understands.
        from app.gateway.fakes import FakeScenario

        try:
            return FakeScenario(value.strip().lower()).value
        except ValueError as exc:
            allowed = ", ".join(sorted(member.value for member in FakeScenario))
            raise ValueError(f"llm_fake_scenario must be one of: {allowed}") from exc

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

    @property
    def secure_cookies(self) -> bool:
        """Effective ``Secure`` cookie flag.

        Explicit ``cookie_secure`` wins; otherwise Secure is forced on only for
        production (HTTPS). Local development over plain HTTP stays off so the
        cookie is actually sent by the browser, documented in the runbook.
        """
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.environment == "production"

    @property
    def allowed_origin_set(self) -> frozenset[str]:
        """Parsed, de-duplicated set of permitted credentialed-request origins."""
        return frozenset(
            origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()
        )


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
