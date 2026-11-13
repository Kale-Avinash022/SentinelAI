"""FastAPI application factory — the modular monolith entry point.

Design rules (Day 006):

- ``create_app(settings, ...)`` builds a fully isolated application; no global
  mutable state, so tests can create many apps with different configurations.
- Importing this module connects to nothing. The lifespan creates the
  SQLAlchemy engine (``sentinel_app`` DSN only) and the Redis client lazily at
  startup and disposes them at shutdown.
- Liveness never depends on dependencies; readiness does (see app.api.health).
- ``app`` at module scope is the uvicorn ASGI target
  (``uvicorn app.main:app``); it is constructed but not started/connected.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.health import CheckResult, build_default_probe_set
from app.api.health import router as health_router
from app.api.v1.router import router as v1_router
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import BodySizeLimitMiddleware, CorrelationIdMiddleware
from app.core.settings import Settings, get_settings
from app.infrastructure.database import create_engine_from_url, dispose_engine

logger = get_logger("app.main")

ProbeSet = dict[str, Callable[[], Awaitable[CheckResult]]]


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build runtime dependencies at startup; always release them at shutdown.

    A dependency being down at boot does NOT prevent startup: liveness must
    stay up so orchestrators keep the process, while /ready reports 503 until
    the dependency recovers.
    """
    settings: Settings = app.state.settings
    probes_overridden: bool = getattr(app.state, "probes_overridden", False)

    if not probes_overridden:
        # Boot must never crash the process: liveness stays up even when a
        # dependency is down; /ready reports the outage instead.
        if settings.database_url:
            try:
                app.state.db_engine = create_engine_from_url(
                    settings.database_url, pool_size=5, max_overflow=10
                )
            except Exception as exc:  # noqa: BLE001 - probe boundary: classify, never leak
                logger.warning(
                    "database engine could not be created",
                    extra={"event": "db_boot", "exc": type(exc).__name__},
                )
        if settings.redis_url:
            try:
                import redis.asyncio as aioredis

                app.state.redis_client = aioredis.from_url(
                    settings.redis_url, socket_connect_timeout=2, socket_timeout=2
                )
            except Exception as exc:  # noqa: BLE001 - boot must not crash the process
                logger.warning(
                    "redis client could not be created",
                    extra={"event": "redis_boot", "exc": type(exc).__name__},
                )
        app.state.readiness_checks = build_default_probe_set(app)

    logger.info(
        "application startup complete",
        extra={"event": "startup", "environment": settings.environment},
    )
    yield

    if app.state.redis_client is not None:
        await app.state.redis_client.aclose()
    if app.state.db_engine is not None:
        await dispose_engine(app.state.db_engine)
    logger.info("application shutdown complete", extra={"event": "shutdown"})


def create_app(
    settings: Settings | None = None,
    *,
    readiness_probes: ProbeSet | None = None,
) -> FastAPI:
    """Application factory.

    ``settings`` defaults to :func:`get_settings` (environment read happens
    here, never at import). ``readiness_probes`` lets tests and future
    environments replace the dependency probe set wholesale.
    """
    cfg = settings if settings is not None else get_settings()
    configure_logging(cfg.log_level)

    app = FastAPI(
        title="SentinelAI",
        version=__version__,
        description="Enterprise AI Operations (AIOps) and Reliability Platform.",
        lifespan=_lifespan,
        docs_url="/docs" if cfg.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if cfg.docs_enabled else None,
    )

    app.state.settings = cfg
    app.state.db_engine = None
    app.state.redis_client = None
    if readiness_probes is not None:
        app.state.readiness_checks = readiness_probes
        app.state.probes_overridden = True
    else:
        app.state.readiness_checks = {}
        app.state.probes_overridden = False

    # Middleware stack (outermost -> innermost): correlation -> body-size.
    # ``add_middleware`` prepends, so the last registration is outermost:
    # CorrelationId must run first so the 413 path already has a request id.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=cfg.max_request_bytes)
    app.add_middleware(CorrelationIdMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(v1_router)
    return app


# Module-scope ASGI target for ``uvicorn app.main:app``. Constructing it reads
# configuration only; no connection or background task starts until lifespan.
app = create_app()
