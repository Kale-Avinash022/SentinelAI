"""Day 005 integration tests: Alembic round trip and session lifecycle.

These run against a disposable ``sentinel_ai_test`` database created by the
schema-owner role (``sentinel_migrator`` has CREATEDB) — the development
database is never dropped or mutated by this suite. Requires the local Docker
core stack (``docker compose --profile core up -d``); CI runs unit tests only.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.infrastructure.database import (
    application_database_url,
    create_session_factory,
    migration_database_url,
    session_scope,
)
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio(loop_scope="module")

TEST_DB = "sentinel_ai_test"
REPO_ROOT = (
    Path(__file__).resolve().parents[3]
)  # .../SentinelAI (file is backend/tests/integration/)
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic.ini"
BASELINE_REVISION = "0001_baseline"


def _render(url: URL) -> str:
    """Full DSN string with the password intact (SQLAlchemy 2.1 ``str(url)`` masks it)."""
    return url.render_as_string(hide_password=False)


def _alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    # env.py prefers a programmatically supplied URL over the environment DSN.
    config.set_main_option("sqlalchemy.url", url)
    return config


async def _alembic(url: str, action: str, revision: str) -> None:
    # env.py calls asyncio.run internally, so execute it in a worker thread
    # where no event loop is running.
    fn = command.upgrade if action == "upgrade" else command.downgrade
    await asyncio.to_thread(fn, _alembic_config(url), revision)


async def _table_exists(engine: AsyncEngine, table: str) -> bool:
    async with engine.connect() as conn:
        found = await conn.execute(sa.text("SELECT to_regclass(:t)"), {"t": f"public.{table}"})
    return found.scalar() is not None


def _checked_out(engine: AsyncEngine) -> int:
    """Connections currently checked out; ``checkedout()`` exists on every pool at runtime."""
    result: int = engine.pool.checkedout()  # type: ignore[attr-defined]
    return result


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def migrated_test_database() -> AsyncIterator[dict[str, str]]:
    """Create sentinel_ai_test, grant app-role access, upgrade to head, yield DSNs."""
    migrator_url = make_url(migration_database_url())
    app_url = make_url(application_database_url())
    app_user = str(app_url.username)
    assert app_user == "sentinel_app", "application DSN must connect as sentinel_app"
    assert migrator_url.username == "sentinel_migrator", "migration DSN must use sentinel_migrator"

    # 1. Recreate the disposable database as the migration owner.
    admin = create_async_engine(
        migrator_url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    async with admin.connect() as conn:
        await conn.execute(
            sa.text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :db AND pid <> pg_backend_pid()"
            ),
            {"db": TEST_DB},
        )
        await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{TEST_DB}"'))
        await conn.execute(sa.text(f'CREATE DATABASE "{TEST_DB}"'))
    await admin.dispose()

    # 2. Connect to the test database and grant least-privilege access to
    #    sentinel_app (default privileges from init-roles.sh are per-database,
    #    so they must be re-declared inside the new database).
    test_migrator_url = migrator_url.set(database=TEST_DB)
    mig_engine = create_async_engine(
        test_migrator_url, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    async with mig_engine.connect() as conn:
        await conn.execute(sa.text(f'GRANT CONNECT ON DATABASE "{TEST_DB}" TO {app_user}'))
        await conn.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {app_user}"))
        await conn.execute(
            sa.text(
                "ALTER DEFAULT PRIVILEGES FOR ROLE sentinel_migrator IN SCHEMA public "
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app_user}"
            )
        )
        await conn.execute(
            sa.text(
                "ALTER DEFAULT PRIVILEGES FOR ROLE sentinel_migrator IN SCHEMA public "
                f"GRANT USAGE, SELECT ON SEQUENCES TO {app_user}"
            )
        )
    await mig_engine.dispose()

    # 3. Alembic baseline upgrade (migrator identity only).
    await _alembic(_render(test_migrator_url), "upgrade", "head")

    yield {
        "migrator_url": _render(test_migrator_url),
        "app_url": _render(app_url.set(database=TEST_DB)),
    }

    # 4. Teardown: drop the whole disposable database.
    admin = create_async_engine(
        migrator_url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    async with admin.connect() as conn:
        await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
    await admin.dispose()


async def test_upgrade_is_repeatable_and_records_one_current_revision(
    migrated_test_database: dict[str, str],
) -> None:
    """Acceptance: 'Alembic upgrade is repeatable and records one current revision.'"""
    url = migrated_test_database["migrator_url"]
    await _alembic(url, "upgrade", "head")  # second apply must be a no-op, not an error

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            rows = (
                (await conn.execute(sa.text("SELECT version_num FROM alembic_version")))
                .scalars()
                .all()
            )
            seeded = (
                (await conn.execute(sa.text("SELECT version FROM schema_info"))).scalars().all()
            )
    finally:
        await engine.dispose()

    assert rows == [BASELINE_REVISION], "exactly one current revision must be stamped"
    assert seeded == ["1"], "baseline seed row must be present"


async def test_downgrade_upgrade_round_trip(migrated_test_database: dict[str, str]) -> None:
    """Acceptance: 'round-trip migration test uses an isolated database.'"""
    url = migrated_test_database["migrator_url"]
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        await _alembic(url, "downgrade", "base")
        assert not await _table_exists(engine, "schema_info")
        # Alembic empties but does not drop its own version table on downgrade.
        async with engine.connect() as conn:
            stamped = (
                (await conn.execute(sa.text("SELECT version_num FROM alembic_version")))
                .scalars()
                .all()
            )
        assert stamped == []

        await _alembic(url, "upgrade", "head")
        assert await _table_exists(engine, "schema_info")
    finally:
        await engine.dispose()


async def test_application_connections_use_sentinel_app_without_ddl_power(
    migrated_test_database: dict[str, str],
) -> None:
    """Acceptance: 'application connections use sentinel_app, migrations use sentinel_migrator.'"""
    engine = create_async_engine(
        migrated_test_database["app_url"], pool_pre_ping=True, poolclass=NullPool
    )
    factory = create_session_factory(engine)
    try:
        async with session_scope(factory) as session:
            current_user = (await session.execute(sa.text("SELECT current_user"))).scalar()
            is_superuser = (
                await session.execute(sa.text("SELECT current_setting('is_superuser')"))
            ).scalar()
            bypassrls = (
                await session.execute(
                    sa.text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
                )
            ).scalar()
        assert current_user == "sentinel_app"
        assert is_superuser == "off"
        assert bypassrls is False

        # The application identity must not be able to run DDL.
        with pytest.raises(DBAPIError):
            async with session_scope(factory) as session:
                await session.execute(sa.text("CREATE TABLE app_must_not_ddl (id int)"))
    finally:
        await engine.dispose()


async def test_sessions_are_returned_cleanly_to_the_pool(
    migrated_test_database: dict[str, str],
) -> None:
    """Acceptance: 'sessions and connections are returned cleanly to the pool.'"""
    # Default (QueuePool) pooling, small pool, to prove reuse and release.
    engine = create_async_engine(migrated_test_database["app_url"], pool_size=2, max_overflow=0)
    factory = create_session_factory(engine)
    try:
        for _ in range(10):  # repeatedly open and close sessions
            async with session_scope(factory) as session:
                assert (await session.execute(sa.text("SELECT 1"))).scalar() == 1
        assert _checked_out(engine) == 0, "no connection may remain checked out"

        # A rolled-back scope must not leak the connection either.
        with pytest.raises(RuntimeError):
            async with session_scope(factory) as session:
                await session.execute(sa.text("SELECT 1"))
                raise RuntimeError("simulated failure mid-transaction")
        assert _checked_out(engine) == 0
    finally:
        await engine.dispose()
        assert _checked_out(engine) == 0
