"""Liveness and readiness endpoints with an explicit, documented split.

- ``GET /health`` (liveness): process-health only. It must NOT depend on
  databases, caches or queues — restarting a pod because Postgres is slow
  destroys capacity without fixing anything.
- ``GET /ready`` (readiness): can this instance safely serve traffic right
  now? Probes each configured dependency with a short bounded check and
  answers 503 in the standard error envelope when any check fails.

Probe callables live on ``app.state`` so tests (and future dependency
groups) can substitute fakes without monkeypatching internals.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import __version__
from app.core.errors import envelope_response
from app.core.logging import get_logger

logger = get_logger("app.health")

router = APIRouter(tags=["health"])


class CheckResult(BaseModel):
    """One readiness dependency probe result (metadata only, never a DSN)."""

    name: str
    ok: bool
    latency_ms: float | None = None
    reason: str | None = None


# Request-free probe factories; the lifespan wires closures that already
# captured the engine/client, tests can replace the whole set.
ProbeSet = dict[str, Callable[[], Awaitable[CheckResult]]]


class LivenessResponse(BaseModel):
    status: Literal["alive"] = "alive"
    service: str = "sentinel-ai"
    version: str = __version__
    environment: str
    request_id: str


class ReadinessResponse(BaseModel):
    status: Literal["ready"] = "ready"
    service: str = "sentinel-ai"
    version: str = __version__
    checks: list[CheckResult]
    request_id: str


async def _check_database(app: FastAPI) -> CheckResult:
    engine: Any = app.state.db_engine
    if engine is None:
        return CheckResult(name="database", ok=False, reason="not configured")
    started = time.perf_counter()
    try:
        from sqlalchemy import text

        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - probe boundary: classify, never leak
        logger.warning(
            "readiness probe failed", extra={"event": "db_check", "exc": type(exc).__name__}
        )
        return CheckResult(name="database", ok=False, reason="unreachable")
    return CheckResult(
        name="database", ok=True, latency_ms=round((time.perf_counter() - started) * 1000, 2)
    )


async def _check_redis(app: FastAPI) -> CheckResult:
    client: Any = app.state.redis_client
    if client is None:
        return CheckResult(name="redis", ok=False, reason="not configured")
    started = time.perf_counter()
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001 - probe boundary: classify, never leak
        logger.warning(
            "readiness probe failed", extra={"event": "redis_check", "exc": type(exc).__name__}
        )
        return CheckResult(name="redis", ok=False, reason="unreachable")
    return CheckResult(
        name="redis", ok=True, latency_ms=round((time.perf_counter() - started) * 1000, 2)
    )


def build_default_probe_set(app: FastAPI) -> ProbeSet:
    """Standard probes for the configured runtime dependencies."""
    return {
        "database": lambda: _check_database(app),
        "redis": lambda: _check_redis(app),
    }


@router.get("/health", response_model=LivenessResponse, summary="Liveness (process health)")
async def liveness(request: Request) -> LivenessResponse:
    """Reports that this process is alive and its event loop responsive.
    Deliberately touches no external dependency; never returns 503."""
    return LivenessResponse(
        environment=request.app.state.settings.environment,
        request_id=request.state.request_id,
    )


@router.get(
    "/ready",
    summary="Readiness (dependencies)",
    response_model=ReadinessResponse,
    responses={503: {"description": "A readiness dependency is unavailable (error envelope)."}},
)
async def readiness(request: Request) -> JSONResponse:
    """Runs every configured dependency probe concurrently with a bounded
    timeout. All pass -> 200; any fail -> 503 standard error envelope."""
    settings = request.app.state.settings
    checks: ProbeSet = request.app.state.readiness_checks
    results: list[CheckResult] = []
    for name, coro_factory in checks.items():
        try:
            outcome = await asyncio.wait_for(
                coro_factory(), timeout=settings.readiness_timeout_seconds
            )
        except TimeoutError:
            outcome = CheckResult(name=name, ok=False, reason="timeout")
        results.append(outcome)

    failed = [result for result in results if not result.ok]
    if failed:
        return envelope_response(
            request,
            503,
            "dependency_unavailable",
            "One or more readiness dependencies are unavailable.",
            retryable=True,
            failed_checks=[result.name for result in failed],
        )
    return JSONResponse(
        status_code=200,
        content=ReadinessResponse(checks=results, request_id=request.state.request_id).model_dump(),
    )
