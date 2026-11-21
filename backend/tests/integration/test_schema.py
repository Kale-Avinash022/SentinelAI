"""Day 009 integration tests: applied tenant schema, constraints and grants.

Proves the four Day 009 acceptance checks against a disposable
``sentinel_ai_test`` database that is created, migrated and dropped by the
schema-owner role (``sentinel_migrator`` holds CREATEDB); the development
database is never mutated here. Requires the local Docker core stack
(``docker compose --profile core up -d``); CI runs unit tests only.

The privilege-boundary assertions deliberately run as ``sentinel_app`` so a
future widening of grants, or a migration that forgets to narrow the first-boot
default privileges, fails the gate rather than silently passing.
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
    migration_database_url,
    set_tenant_context,
)
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio(loop_scope="module")

TEST_DB = "sentinel_ai_test"
REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic.ini"
# The current head after Day 014 (immutable config/prompt version history +
# CAS activation records, with their least-privilege column-level grants, RLS
# policies and the widened audit secret-material CHECK; on top of Day 013's
# hashed scoped API keys + append-only audit trail, and Day 012's rotating
# refresh sessions) — the schema/seed tests assert the isolated database is
# migrated all the way to this revision.
TENANT_REVISION = "0007_config_prompt_versions"
# Deterministic demo organization seeded by revision 0002 (ADR-0004).
DEMO_ORG_ID = "a1000000-0000-4000-8000-000000000001"

DAY009_TABLES = {
    "organizations",
    "users",
    "role_assignments",
    "applications",
    "provider_configs",
    "provider_credentials",
    "provider_catalog_entries",
    "model_catalog_entries",
}
CONSTRAINT_NAMES = {
    "fk_role_assignments_user_org_users",
    "fk_applications_owner_user_org_users",
    "uq_users_id_org",
    "ck_role_assignments_valid_role",
    "ck_provider_credentials_secret_ref_is_locator_not_payload",
    "uq_provider_credentials_org_provider",
}


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    return config


async def _alembic(url: str, revision: str) -> None:
    # env.py calls asyncio.run internally, so run it in a worker thread that
    # has no running event loop.
    await asyncio.to_thread(command.upgrade, _alembic_config(url), revision)


def _is_permission_denied(exc: BaseException) -> bool:
    """PostgreSQL 42501: the statement was valid but the role lacks privilege."""
    orig = getattr(exc, "orig", exc)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate == "42501":
        return True
    return "permission denied" in str(exc).lower()


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def tenant_schema() -> AsyncIterator[dict[str, str]]:
    """Create sentinel_ai_test, migrate to head, yield migrator + app DSNs."""
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
    # Broad first-boot-style default privileges on purpose: the migration must
    # narrow them to least-privilege regardless of this setup.
    mig = create_async_engine(test_migrator_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    async with mig.connect() as conn:
        await conn.execute(sa.text(f'GRANT CONNECT ON DATABASE "{TEST_DB}" TO sentinel_app'))
        await conn.execute(sa.text("GRANT USAGE ON SCHEMA public TO sentinel_app"))
        await conn.execute(
            sa.text(
                "ALTER DEFAULT PRIVILEGES FOR ROLE sentinel_migrator IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sentinel_app"
            )
        )
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


# ---------------------------------------------------------------------------
# Acceptance: foreign keys and tenant indexes exist as designed
# ---------------------------------------------------------------------------


async def test_all_day009_tables_present(tenant_schema: dict[str, str]) -> None:
    engine = create_async_engine(tenant_schema["migrator_url"], poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
            ).scalars()
            assert DAY009_TABLES <= set(rows)
            stamped = (
                (await conn.execute(sa.text("SELECT version_num FROM alembic_version")))
                .scalars()
                .all()
            )
            assert stamped == [TENANT_REVISION]
    finally:
        await engine.dispose()


async def test_composite_fks_checks_and_org_indexes_exist(tenant_schema: dict[str, str]) -> None:
    engine = create_async_engine(tenant_schema["migrator_url"], poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            connames = set(
                (
                    await conn.execute(
                        sa.text(
                            "SELECT conname FROM pg_constraint WHERE connamespace = "
                            "current_schema()::regnamespace"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert CONSTRAINT_NAMES <= connames, CONSTRAINT_NAMES - connames
            indexed = set(
                (
                    await conn.execute(
                        sa.text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
                    )
                )
                .scalars()
                .all()
            )
            for table in (
                "users",
                "role_assignments",
                "applications",
                "provider_configs",
                "provider_credentials",
            ):
                assert f"ix_{table}_organization_id" in indexed, table
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Acceptance: duplicate/invalid domain rows are rejected by the database
# ---------------------------------------------------------------------------


async def test_database_rejects_invalid_domain_rows(tenant_schema: dict[str, str]) -> None:
    """Constraint verdicts are checked on the admin/migration role, which holds
    full DML and (since Day 010) ``BYPASSRLS``. Running here rather than as
    ``sentinel_app`` keeps the assertion meaningful under forced row-level
    security: an invalid write fails on a *constraint*, not on the RLS boundary,
    so a genuine schema defect cannot be masked by the tenant filter.
    """
    engine = create_async_engine(tenant_schema["migrator_url"], poolclass=NullPool)

    async def attempt(label: str, sql: str) -> None:
        try:
            async with engine.begin() as conn:
                await conn.execute(sa.text(sql))
        except IntegrityError as exc:
            assert _is_permission_denied(exc) is False, f"{label}: expected constraint, got priv"
            return
        raise AssertionError(f"{label}: invalid row was accepted")

    try:
        # Duplicate slug (unique).
        await attempt(
            "dup slug",
            "INSERT INTO organizations (id, name, slug, is_active, allow_self_approval, "
            "created_at, updated_at) VALUES (gen_random_uuid(), 'copy', 'sentinel-demo', "
            "true, false, now(), now())",
        )
        # Unknown role value (CHECK).
        await attempt(
            "bad role",
            "INSERT INTO role_assignments (id, user_id, organization_id, role, created_at) "
            "VALUES (gen_random_uuid(), 'a2000000-0000-4000-8000-000000000001', "
            "'a1000000-0000-4000-8000-000000000001', 'superuser', now())",
        )
        # Cross-tenant role grant: real user paired with a foreign org id
        # (composite FK to users(id, organization_id)).
        await attempt(
            "cross-tenant role",
            "INSERT INTO role_assignments (id, user_id, organization_id, role, created_at) "
            "VALUES (gen_random_uuid(), 'a2000000-0000-4000-8000-000000000001', "
            "gen_random_uuid(), 'admin', now())",
        )
        # Inline credential instead of a secret reference (CHECK).
        await attempt(
            "json secret",
            "INSERT INTO provider_credentials (id, organization_id, provider, secret_ref, "
            "status, created_at) VALUES (gen_random_uuid(), "
            "'a1000000-0000-4000-8000-000000000001', 'openai', '{\"key\": \"sk-live\"}', "
            "'active', now())",
        )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Acceptance: sentinel_app cannot alter schema or grant itself privileges
# ---------------------------------------------------------------------------

DENIALS: dict[str, str] = {
    "ddl_create": "CREATE TABLE app_must_not_ddl (id int)",
    "ddl_alter": "ALTER TABLE organizations ADD COLUMN sneaky text",
    "ddl_drop": "DROP TABLE users",
    "truncate": "TRUNCATE TABLE role_assignments",
    "tenant_delete": "DELETE FROM users WHERE organization_id = "
    "'a1000000-0000-4000-8000-000000000001'",
    "catalog_write": "INSERT INTO provider_catalog_entries (id, provider, display_name, "
    "supports_streaming, supports_chat, is_deterministic_mock, created_at) "
    "VALUES (gen_random_uuid(), 'rogue', 'Rogue', false, true, false, now())",
}


@pytest.mark.parametrize("sql", list(DENIALS.values()), ids=list(DENIALS.keys()))
async def test_sentinel_app_denied_privilege_escalation(
    tenant_schema: dict[str, str], sql: str
) -> None:
    engine = create_async_engine(tenant_schema["app_url"], poolclass=NullPool)
    try:
        with pytest.raises(DBAPIError) as excinfo:
            async with engine.begin() as conn:
                await conn.execute(sa.text(sql))
        assert _is_permission_denied(excinfo.value), str(excinfo.value)
    finally:
        await engine.dispose()


async def test_sentinel_app_cannot_grant_itself_privileges(
    tenant_schema: dict[str, str],
) -> None:
    """A self-GRANT of an unheld privilege is inert: PostgreSQL skips it with a
    warning (no error), so the real invariant is that the privilege was *not*
    acquired — verified via has_table_privilege, not by expecting an exception.
    """
    engine = create_async_engine(tenant_schema["app_url"], poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            # Attempt to widen sentinel_app's own privileges (should do nothing).
            await conn.execute(sa.text("GRANT DELETE ON TABLE users TO sentinel_app"))
            await conn.execute(sa.text("GRANT TRUNCATE ON TABLE role_assignments TO sentinel_app"))
            can_delete = (
                await conn.execute(
                    sa.text("SELECT has_table_privilege(current_user, 'users', 'DELETE')")
                )
            ).scalar()
            can_truncate = (
                await conn.execute(
                    sa.text(
                        "SELECT has_table_privilege(current_user, 'role_assignments', 'TRUNCATE')"
                    )
                )
            ).scalar()
        assert can_delete is False, "sentinel_app escalated DELETE on users"
        assert can_truncate is False, "sentinel_app escalated TRUNCATE on role_assignments"
    finally:
        await engine.dispose()


async def test_sentinel_app_can_dml_tenant_rows(tenant_schema: dict[str, str]) -> None:
    """Least-privilege is not least-capability-by-accident: with a correct
    transaction-local tenant context (Day 010), legitimate ``sentinel_app``
    INSERT/UPDATE/SELECT on that organization's rows still succeed."""
    engine = create_async_engine(tenant_schema["app_url"], poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await set_tenant_context(conn, DEMO_ORG_ID)
            await conn.execute(
                sa.text(
                    "INSERT INTO applications (id, organization_id, owner_user_id, name, "
                    "status, created_at, updated_at) VALUES (gen_random_uuid(), "
                    "'a1000000-0000-4000-8000-000000000001', "
                    "'a2000000-0000-4000-8000-000000000001', 'legit-app', 'active', "
                    "now(), now())"
                )
            )
            await conn.execute(
                sa.text(
                    "UPDATE organizations SET name = 'Renamed Demo' WHERE slug = 'sentinel-demo'"
                )
            )
        async with engine.begin() as conn:
            await set_tenant_context(conn, DEMO_ORG_ID)
            can_read = (
                await conn.execute(
                    sa.text("SELECT count(*) FROM role_assignments WHERE role = 'admin'")
                )
            ).scalar()
        assert can_read == 2
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Acceptance: upgrade/downgrade round trip on the isolated database
# ---------------------------------------------------------------------------


async def test_downgrade_and_upgrade_round_trip(tenant_schema: dict[str, str]) -> None:
    url = tenant_schema["migrator_url"]
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        # Downgrade one step: tenant tables vanish, baseline schema_info stays.
        await asyncio.to_thread(command.downgrade, _alembic_config(url), "0001_baseline")
        async with engine.connect() as conn:
            remaining = set(
                (
                    await conn.execute(
                        sa.text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public'"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert not (DAY009_TABLES & remaining), DAY009_TABLES & remaining
        assert "schema_info" in remaining

        # Upgrade again: tables and the demo seed return.
        await _alembic(url, "head")
        async with engine.connect() as conn:
            orgs = (
                (
                    await conn.execute(
                        sa.text("SELECT slug FROM organizations WHERE slug = 'sentinel-demo'")
                    )
                )
                .scalars()
                .all()
            )
            roles = (
                (await conn.execute(sa.text("SELECT role FROM role_assignments ORDER BY role")))
                .scalars()
                .all()
            )
        assert orgs == ["sentinel-demo"]
        assert roles == ["admin", "admin", "owner"]
    finally:
        await engine.dispose()
