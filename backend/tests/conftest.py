"""Shared pytest setup: load the ignored repo-root .env so tests can resolve local DSNs,
and provide FastAPI shell fixtures that never touch real infrastructure."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# Deterministic placeholder used by factory-built test apps only.
TEST_SECRET_KEY = "test-secret-key-0123456789-0123456789-0123"


def make_settings(**overrides: Any) -> Any:
    """Build Settings without env files; ``_env_file`` is init-only in
    pydantic-settings and invisible to mypy, hence the Any-typed factory."""
    from app.core.settings import Settings

    kwargs: dict[str, Any] = {"_env_file": None}
    kwargs.update(overrides)
    return Settings(**kwargs)


@pytest.fixture
def shell_settings() -> Any:
    """Settings for shell tests: no DSNs, tiny body bound, docs schemas on."""
    return make_settings(
        environment="test",
        log_level="INFO",
        secret_key=TEST_SECRET_KEY,
        database_url="",
        redis_url="",
        max_request_bytes=1024,
        docs_enabled=True,
    )


class ReadyProbe:
    """Mutable fake probe set injected instead of real DB/Redis checks."""

    def __init__(self) -> None:
        self.db_ok = True
        self.redis_ok = True

    def probe_set(self) -> dict[str, Any]:
        from app.api.health import CheckResult

        async def database() -> Any:
            return CheckResult(
                name="database", ok=self.db_ok, reason=None if self.db_ok else "unreachable"
            )

        async def redis() -> Any:
            return CheckResult(
                name="redis", ok=self.redis_ok, reason=None if self.redis_ok else "unreachable"
            )

        return {"database": database, "redis": redis}


@pytest.fixture
def ready_probe() -> ReadyProbe:
    return ReadyProbe()


@pytest.fixture
def shell_app(shell_settings: Any, ready_probe: ReadyProbe) -> Any:
    """A fresh FastAPI app with fake readiness probes: no connections ever made."""
    from app.main import create_app

    return create_app(shell_settings, readiness_probes=ready_probe.probe_set())


@pytest.fixture
async def shell_client(shell_app: Any) -> AsyncIterator[Any]:
    """httpx AsyncClient over ASGITransport (the TestClient successor)."""
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=shell_app), base_url="http://shell"
    ) as client:
        yield client


@pytest.fixture
def minimal_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Hide all configuration env vars so settings-validation tests are hermetic."""
    for key in (
        "SECRET_KEY",
        "DATABASE_URL",
        "REDIS_URL",
        "ENVIRONMENT",
        "LOG_LEVEL",
        "API_MAX_REQUEST_BYTES",
        "ASYNC_TRANSPORT",
    ):
        monkeypatch.delenv(key, raising=False)
    # Settings reads .env relative to CWD; move off the repo root for this test.
    monkeypatch.chdir(Path(os.__file__).parent)
    yield
