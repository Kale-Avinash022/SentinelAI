"""Day 010 integration tests: forced PostgreSQL row-level security.

Proves the four Day 010 acceptance checks against a disposable
``sentinel_ai_test`` database migrated to head (revision ``0003_rls_policies``).
The development database is never mutated. Requires the local Docker core stack
(``docker compose --profile core up -d``); CI runs unit tests only.

The isolation assertions deliberately run as the API role ``sentinel_app``
(``NOBYPASSRLS``) so that a policy accidentally dropped, un-forced, or widened
to bypass fails the gate rather than silently passing. Cross-tenant fixture data
is written through the admin/migration role ``sentinel_migrator``, which holds
``BYPASSRLS`` (the separate, audited admin path) — establishing rows for two
organizations so the boundary can be observed from the API side.
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
    TENANT_ORGANIZATION_GUC,
    TenantContextError,
    application_database_url,
    create_session_factory,
    current_tenant_context,
    migration_database_url,
    set_tenant_context,
    tenant_connection_scope,
    tenant_session_scope,
)
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio(loop_scope="module")

TEST_DB = "sentinel_ai_test"
REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic.ini"
RLS_REVISION = "0003_rls_policies"

# Tenant-owned, RLS-protected tables (must match revision 0003).
TENANT_TABLES = (
    "organizations",
    "users",
    "role_assignments",
    "applications",
    "provider_configs",
    "provider_credentials",
)
# Global read-only reference data: intentionally NOT under RLS.
CATALOG_TABLES = ("provider_catalog_entries", "model_catalog_entries")

# Two synthetic organizations, each with a user, a role and an application.
ORG_A = "b1000000-0000-4000-8000-00000000000a"
ORG_B = "b1000000-0000-4000-8000-00000000000b"
USER_A = "b2000000-0000-4000-8000-00000000000a"
USER_B = "b2000000-0000-4000-8000-00000000000b"
ROLE_A = "b4000000-0000-4000-8000-00000000000a"
ROLE_B = "b4000000-0000-4000-8000-00000000000b"
APP_A = "b3000000-0000-4000-8000-00000000000a"
APP_B = "b3000000-0000-4000-8000-00000000000b"


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    return config


async def _alembic(url: str, revision: str) -> None:
    # env.py calls asyncio.run internally, so run it in a worker thread that has
    # no running event loop.
    await asyncio.to_thread(command.upgrade, _alembic_config(url), revision)


def _is_permission_denied(exc: BaseException) -> bool:
    """PostgreSQL 42501 covers both privilege and row-level-security denials."""
    orig = getattr(exc, "orig", exc)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate == "42501":
        return True
    text_ = str(exc).lower()
    return "permission denied" in text_ or "row-level security" in text_


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def rls_schema() -> AsyncIterator[dict[str, str]]:
    """Create sentinel_ai_test, migrate to head (RLS applied), yield DSNs."""
    migrator_url = make_url(migration_database_url())
    app_url = make_url(application_database_url())
    assert migrator_url.username == "sentinel_migrator"
    assert str(app_url.username) == "sentinel_app"

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

    test_migrator_url = migrator_url.set(database=TEST_DB)
    mig = create_async_engine(test_migrator_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    async with mig.connect() as conn:
        await conn.execute(sa.text(f'GRANT CONNECT ON DATABASE "{TEST_DB}" TO sentinel_app'))
        await conn.execute(sa.text("GRANT USAGE ON SCHEMA public TO sentinel_app"))
    await mig.dispose()

    await _alembic(_render(test_migrator_url), "head")

    yield {
        "migrator_url": _render(test_migrator_url),
        "app_url": _render(app_url.set(database=TEST_DB)),
    }

    admin = create_async_engine(
        migrator_url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    async with admin.connect() as conn:
        await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
    await admin.dispose()


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def two_tenants(rls_schema: dict[str, str]) -> None:
    """Seed org A and org B (each a user + role + app) via the admin role.

    ``sentinel_migrator`` holds ``BYPASSRLS`` so it can write rows for both
    organizations in one connection — exactly what the API role must never do.
    """
    engine = create_async_engine(rls_schema["migrator_url"], poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for org, user, role, app in (
                (ORG_A, USER_A, ROLE_A, APP_A),
                (ORG_B, USER_B, ROLE_B, APP_B),
            ):
                await conn.execute(
                    sa.text(
                        "INSERT INTO organizations (id, name, slug, is_active, "
                        "allow_self_approval, created_at, updated_at) VALUES "
                        f"('{org}', 'Org {org[-1]}', 'org-{org[-1]}', true, false, now(), now())"
                    )
                )
                await conn.execute(
                    sa.text(
                        "INSERT INTO users (id, organization_id, email, display_name, "
                        "password_hash, is_active, created_at, updated_at) VALUES "
                        f"('{user}', '{org}', 'u{org[-1]}@rls.test', 'User', NULL, true, "
                        "now(), now())"
                    )
                )
                await conn.execute(
                    sa.text(
                        "INSERT INTO role_assignments (id, user_id, organization_id, role, "
                        "created_at) VALUES "
                        f"('{role}', '{user}', '{org}', 'owner', now())"
                    )
                )
                await conn.execute(
                    sa.text(
                        "INSERT INTO applications (id, organization_id, owner_user_id, name, "
                        "status, created_at, updated_at) VALUES "
                        f"('{app}', '{org}', '{user}', 'app-{org[-1]}', 'active', now(), now())"
                    )
                )
    finally:
        await engine.dispose()


async def _app_scalar(rls_schema: dict[str, str], org: str | None, sql: str) -> object:
    """Run one read as ``sentinel_app`` with ``org`` context (None = missing)."""
    engine = create_async_engine(rls_schema["app_url"], poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            if org is not None:
                await set_tenant_context(conn, org)
            return (await conn.execute(sa.text(sql))).scalar()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Acceptance 1: RLS is enabled AND forced on every tenant-owned table
# ---------------------------------------------------------------------------


async def test_rls_enabled_and_forced_on_all_tenant_tables(rls_schema: dict[str, str]) -> None:
    engine = create_async_engine(rls_schema["migrator_url"], poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            flags = {
                row[0]: (row[1], row[2])
                for row in (
                    await conn.execute(
                        sa.text(
                            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                            "WHERE relkind = 'r' AND relnamespace = current_schema()::regnamespace"
                        )
                    )
                ).all()
            }
        for table in TENANT_TABLES:
            enabled, forced = flags[table]
            assert enabled is True, f"{table}: RLS not enabled"
            assert forced is True, f"{table}: RLS not forced (owner could bypass)"
        # Global catalog must stay unrestricted.
        for table in CATALOG_TABLES:
            enabled, _ = flags[table]
            assert enabled is False, f"{table}: catalog should not be under RLS"
    finally:
        await engine.dispose()


async def test_tenant_isolation_policy_exists_on_every_table(
    rls_schema: dict[str, str],
) -> None:
    engine = create_async_engine(rls_schema["migrator_url"], poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            policies = set(
                (
                    await conn.execute(
                        sa.text("SELECT policyname FROM pg_policies WHERE schemaname = 'public'")
                    )
                )
                .scalars()
                .all()
            )
        for table in TENANT_TABLES:
            assert f"{table}_tenant_isolation" in policies, table
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Acceptance: a query in one tenant context sees only that tenant's rows
# ---------------------------------------------------------------------------


async def test_context_sees_only_own_tenant_rows(
    rls_schema: dict[str, str], two_tenants: None
) -> None:
    for org, own, other in ((ORG_A, APP_A, APP_B), (ORG_B, APP_B, APP_A)):
        engine = create_async_engine(rls_schema["app_url"], poolclass=NullPool)
        try:
            async with tenant_connection_scope(engine, org) as conn:
                ids = set(
                    (await conn.execute(sa.text("SELECT id FROM applications"))).scalars().all()
                )
        finally:
            await engine.dispose()
        assert str(own) in {str(i) for i in ids}, (org, ids)
        assert str(other) not in {str(i) for i in ids}, (org, ids)
        # Only the current org's own row is visible in organizations.
        visible_orgs = await _app_scalar(rls_schema, org, "SELECT count(*) FROM organizations")
        assert visible_orgs == 1, org


# ---------------------------------------------------------------------------
# Acceptance 2: missing tenant context exposes no rows and blocks writes
# ---------------------------------------------------------------------------


async def test_missing_context_exposes_no_tenant_rows(
    rls_schema: dict[str, str], two_tenants: None
) -> None:
    engine = create_async_engine(rls_schema["app_url"], poolclass=NullPool)
    try:
        async with engine.begin() as conn:  # deliberately no SET of context
            for table in ("organizations", "users", "role_assignments", "applications"):
                count = (await conn.execute(sa.text(f"SELECT count(*) FROM {table}"))).scalar()
                assert count == 0, f"{table} leaked {count} rows without tenant context"
    finally:
        await engine.dispose()


async def test_missing_context_blocks_write(rls_schema: dict[str, str], two_tenants: None) -> None:
    engine = create_async_engine(rls_schema["app_url"], poolclass=NullPool)
    try:
        with pytest.raises(DBAPIError) as excinfo:
            async with engine.begin() as conn:  # no context -> WITH CHECK denies
                await conn.execute(
                    sa.text(
                        "INSERT INTO applications (id, organization_id, owner_user_id, name, "
                        "status, created_at, updated_at) VALUES (gen_random_uuid(), "
                        f"'{ORG_A}', '{USER_A}', 'no-context', 'active', now(), now())"
                    )
                )
        assert _is_permission_denied(excinfo.value), str(excinfo.value)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Acceptance: a cross-tenant write (foreign organization_id) is rejected
# ---------------------------------------------------------------------------


async def test_cross_tenant_write_is_rejected(
    rls_schema: dict[str, str], two_tenants: None
) -> None:
    engine = create_async_engine(rls_schema["app_url"], poolclass=NullPool)
    try:
        with pytest.raises(DBAPIError) as excinfo:
            # Context is A, but the row claims organization B.
            async with tenant_connection_scope(engine, ORG_A) as conn:
                await conn.execute(
                    sa.text(
                        "INSERT INTO applications (id, organization_id, owner_user_id, name, "
                        "status, created_at, updated_at) VALUES (gen_random_uuid(), "
                        f"'{ORG_B}', '{USER_B}', 'leak', 'active', now(), now())"
                    )
                )
        assert _is_permission_denied(excinfo.value), str(excinfo.value)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Acceptance 3: a pooled connection cannot carry one tenant into the next
# ---------------------------------------------------------------------------


async def test_pooled_connection_resets_transaction_local_context(
    rls_schema: dict[str, str], two_tenants: None
) -> None:
    """One real pool (size 1) forces the same connection to be reused.

    Transaction-local ``set_config`` is discarded at commit, so after an
    organization-A request the returned connection has *no* context, and an
    organization-B request on the same connection sees only B — never A.
    """
    engine = create_async_engine(rls_schema["app_url"], poolclass=None, pool_size=1, max_overflow=0)
    try:
        # Request 1: tenant A.
        async with tenant_connection_scope(engine, ORG_A) as conn:
            a_visible = (await conn.execute(sa.text("SELECT count(*) FROM applications"))).scalar()
        assert a_visible == 1

        # Reused connection, brand-new transaction: context must be gone.
        async with engine.begin() as conn:
            leaked = (await conn.execute(sa.text("SELECT count(*) FROM applications"))).scalar()
            assert leaked == 0, "pooled connection leaked org A context after commit"
            still = await current_tenant_context(conn)
            assert still is None, f"app.organization_id persisted: {still!r}"

        # Request 2: tenant B on the same pooled connection.
        async with tenant_connection_scope(engine, ORG_B) as conn:
            b_ids = {
                str(i)
                for i in (await conn.execute(sa.text("SELECT id FROM applications"))).scalars()
            }
        assert b_ids == {APP_B}, b_ids
    finally:
        await engine.dispose()


async def test_session_scope_sets_and_commits_context(
    rls_schema: dict[str, str], two_tenants: None
) -> None:
    engine = create_async_engine(rls_schema["app_url"], poolclass=NullPool)
    factory = create_session_factory(engine)
    try:
        async with tenant_session_scope(factory, ORG_A) as session:
            applied = await current_tenant_context(session)
            assert applied == ORG_A
            count = (await session.execute(sa.text("SELECT count(*) FROM applications"))).scalar()
            assert count == 1
    finally:
        await engine.dispose()


async def test_tenant_session_scope_rejects_missing_context() -> None:
    """The dependency validates context before opening any connection."""
    factory = create_session_factory(create_async_engine("postgresql+asyncpg://x/y@localhost/z"))
    with pytest.raises(TenantContextError):
        async with tenant_session_scope(factory, ""):  # empty context -> no DB touch
            pass


async def test_tenant_session_scope_rejects_malformed_context() -> None:
    factory = create_session_factory(create_async_engine("postgresql+asyncpg://x/y@localhost/z"))
    with pytest.raises(TenantContextError):
        async with tenant_session_scope(factory, "not-a-uuid"):
            pass


# ---------------------------------------------------------------------------
# Acceptance 4: the API role cannot bypass or change the policies
# ---------------------------------------------------------------------------


RLS_ESCALATIONS: dict[str, str] = {
    "disable_rls": "ALTER TABLE users DISABLE ROW LEVEL SECURITY",
    "unforce_rls": "ALTER TABLE users NO FORCE ROW LEVEL SECURITY",
    "drop_policy": "DROP POLICY users_tenant_isolation ON users",
    "create_policy": "CREATE POLICY rogue ON users USING (true)",
    "grant_bypass": "ALTER ROLE sentinel_app BYPASSRLS",
}


@pytest.mark.parametrize("sql", list(RLS_ESCALATIONS.values()), ids=list(RLS_ESCALATIONS.keys()))
async def test_sentinel_app_cannot_change_rls(rls_schema: dict[str, str], sql: str) -> None:
    engine = create_async_engine(rls_schema["app_url"], poolclass=NullPool)
    try:
        with pytest.raises(DBAPIError) as excinfo:
            async with engine.begin() as conn:
                await conn.execute(sa.text(sql))
        assert _is_permission_denied(excinfo.value), str(excinfo.value)
    finally:
        await engine.dispose()


async def test_tenant_guc_name_is_stable(rls_schema: dict[str, str]) -> None:
    # Guards against a silent drift between the policy predicate and the GUC
    # the application sets: both must use the same key.
    assert TENANT_ORGANIZATION_GUC == "app.organization_id"
