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
- Tenant isolation (Day 010): application queries run inside
  :func:`tenant_session_scope`, which sets the ``app.organization_id``
  row-level-security context **transaction-locally** (``set_config(..., true)``).
  A transaction-local value is discarded by PostgreSQL at commit/rollback, so
  the connection handed back to the pool never carries one organization's
  context into another. Missing context is refused, never defaulted.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

MIGRATION_DATABASE_URL_ENV = "MIGRATION_DATABASE_URL"
APPLICATION_DATABASE_URL_ENV = "DATABASE_URL"

# Transaction-local GUC compared against every tenant table's organization
# column by the Day 010 RLS policies (see migrations/versions/0003_rls_policies).
TENANT_ORGANIZATION_GUC = "app.organization_id"

# A tenant identifier may arrive as a UUID or its canonical string form.
TenantId = str | uuid.UUID


class DatabaseConfigurationError(RuntimeError):
    """Raised when a required database DSN is missing from the environment."""


class TenantContextError(ValueError):
    """Raised when a tenant-scoped transaction is opened without valid context."""


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
    return create_async_engine(migration_database_url(), pool_pre_ping=True, **overrides)


def create_application_engine(**overrides: object) -> AsyncEngine:
    """Engine for application runtime traffic as ``sentinel_app``."""
    return create_async_engine(application_database_url(), pool_pre_ping=True, **overrides)


def create_engine_from_url(url: str, **overrides: object) -> AsyncEngine:
    """Engine for an explicitly supplied DSN (typed-settings path, tests).

    Callers must pass the *application* role URL; upholding the identity
    split remains the responsibility of the settings validator.
    """
    return create_async_engine(url, pool_pre_ping=True, **overrides)


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


def _normalize_organization_id(organization_id: TenantId) -> str:
    """Canonicalize a tenant id to its UUID string, refusing missing/invalid."""
    if isinstance(organization_id, uuid.UUID):
        return str(organization_id)
    raw = organization_id.strip() if organization_id else ""
    if not raw:
        raise TenantContextError(
            "A transaction-local organization_id is required; refusing to run a "
            "tenant-scoped query with missing context (RLS would deny access)."
        )
    try:
        return str(uuid.UUID(raw))
    except ValueError as exc:
        raise TenantContextError(f"organization_id is not a valid UUID: {raw!r}") from exc


async def set_tenant_context(
    executor: AsyncConnection | AsyncSession, organization_id: TenantId
) -> str:
    """Set ``app.organization_id`` TRANSACTION-locally (``is_local=true``).

    Must run inside the same transaction as the queries it protects. PostgreSQL
    discards the value when the transaction ends, so it can never leak through a
    pooled connection. Returns the canonical id that was applied.
    """
    value = _normalize_organization_id(organization_id)
    await executor.execute(
        text("SELECT set_config(:key, :value, true)"),
        {"key": TENANT_ORGANIZATION_GUC, "value": value},
    )
    return value


async def current_tenant_context(executor: AsyncConnection | AsyncSession) -> str | None:
    """Read the current transaction-local context.

    Returns ``None`` when no tenant is active: PostgreSQL reports a never-set GUC
    as NULL and a reverted pooled connection as the empty string, and both mean
    "no organization context" for the RLS policies.
    """
    result = await executor.execute(
        text("SELECT current_setting(:key, true)"), {"key": TENANT_ORGANIZATION_GUC}
    )
    value = result.scalar()
    if value is None:
        return None
    return str(value) or None


@asynccontextmanager
async def tenant_session_scope(
    factory: async_sessionmaker[AsyncSession], organization_id: TenantId
) -> AsyncIterator[AsyncSession]:
    """Short transaction bound to one organization's RLS context.

    Opens a session, starts a transaction, sets the transaction-local
    ``app.organization_id`` **before** the body runs, then commits on success or
    rolls back on any error. The context vanishes with the transaction, so the
    same pooled connection can serve a different tenant next without leaking.
    The context is validated up front, before any connection is opened, so a
    missing or malformed tenant fails cleanly instead of starting work.
    """
    value = _normalize_organization_id(organization_id)
    session = factory()
    try:
        async with session.begin():
            await set_tenant_context(session, value)
            yield session
    finally:
        await session.close()


@asynccontextmanager
async def tenant_connection_scope(
    engine: AsyncEngine, organization_id: TenantId
) -> AsyncIterator[AsyncConnection]:
    """Connection-level twin of :func:`tenant_session_scope` for raw SQL/tests."""
    async with engine.begin() as conn:
        await set_tenant_context(conn, organization_id)
        yield conn
