"""Async SQLAlchemy engine and session lifecycle for SentinelAI.

Design rules (Day 005):

- Nothing connects at import time. Engines are built explicitly by callers
  (FastAPI lifespan, workers, Alembic, tests) via the factory functions below.
- Two DSN identities are read from the environment and never interchanged:

  * ``MIGRATION_DATABASE_URL`` — schema owner (``sentinel_migrator``), DDL only.
  * ``DATABASE_URL``           — runtime application role (``sentinel_app``),
    DML only, non-owner, NOBYPASSRLS. Application code must never connect
    with the migration URL.

- Sessions are short-lived: use :func:`session_scope` so every block ends in
  an explicit commit or rollback and the connection returns to the pool.
  Future tenant context (``SET LOCAL app.current_tenant``) will be set inside
  these transactions.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

MIGRATION_DATABASE_URL_ENV = "MIGRATION_DATABASE_URL"
APPLICATION_DATABASE_URL_ENV = "DATABASE_URL"


class DatabaseConfigurationError(RuntimeError):
    """Raised when a required database DSN is missing from the environment."""


def _require_url(env_var: str) -> str:
    url = os.environ.get(env_var, "").strip()
    if not url:
        raise DatabaseConfigurationError(
            f"{env_var} is not set. Copy .env.example to .env and start the core Docker stack."
        )
    return url


def migration_database_url() -> str:
    """DSN for schema-owning migrations (``sentinel_migrator``). Never used by app runtime."""
    return _require_url(MIGRATION_DATABASE_URL_ENV)


def application_database_url() -> str:
    """DSN for application runtime connections (``sentinel_app``, least-privilege DML)."""
    return _require_url(APPLICATION_DATABASE_URL_ENV)


def create_migration_engine(**overrides: object) -> AsyncEngine:
    """Engine for Alembic/DDL work. Keyword overrides allow test-specific pools."""
    return create_async_engine(migration_database_url(), pool_pre_ping=True, **overrides)  # type: ignore[arg-type]


def create_application_engine(**overrides: object) -> AsyncEngine:
    """Engine for application runtime traffic as ``sentinel_app``."""
    return create_async_engine(application_database_url(), pool_pre_ping=True, **overrides)  # type: ignore[arg-type]


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to ``engine``; sessions manage their own transactions."""
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Short transaction scope: commit on success, rollback on any error, always release.

    Usage::

        async with session_scope(session_factory) as session:
            session.add(...)
        # committed here; on exception the transaction is rolled back
    """
    session = factory()
    try:
        yield session
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine(engine: AsyncEngine) -> None:
    """Return every pooled connection. Call during application/worker shutdown."""
    await engine.dispose()
