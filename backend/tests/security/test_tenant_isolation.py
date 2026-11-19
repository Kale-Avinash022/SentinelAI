"""Day 011 — cross-tenant isolation negative-test suite.

Turns the Day 010 row-level-security boundary into a *repeatable CI invariant*
rather than a one-time manual check. Everything here runs against a real
PostgreSQL (never a mock, never the in-memory-SQLite unit harness that cannot
enforce RLS) and, critically, as the **non-owner API role** ``sentinel_app``
(``NOBYPASSRLS``): if a policy is dropped, un-forced, or widened, or if a
handler forgets a tenant filter, these tests fail.

Design (steps/step-011.md §4):

* Two synthetic organizations seeded through the admin/migration role
  (``sentinel_migrator`` holds ``BYPASSRLS``) with **overlapping identifiers** —
  the same application name in both — so a leak caused by a name collision or a
  forgotten filter is visible.
* Reads/writes are attempted as ``sentinel_app`` under an explicit tenant
  context: direct object references, forged ``organization_id`` bodies, nested
  foreign-key references, deliberate filter removal, and missing context.
* Existence leakage matters, so a foreign object and a never-existing object
  must be *indistinguishable* (both simply return nothing).
* Pool safety is exercised on a real ``pool_size=1`` pool that forces connection
  reuse across alternating tenant requests.
* The nested-reference guard (revision ``0004_nested_tenant_fk``) is asserted
  directly: a config in org A that points at org B's application is rejected by
  the composite foreign key, which RLS alone cannot police.

Requires the local Docker core stack; CI runs this suite against a real
PostgreSQL service container (see ``.github/workflows/ci.yml``). The
development database is never touched — a disposable ``sentinel_ai_test`` is
created and dropped around the module.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.infrastructure.database import (
    application_database_url,
    current_tenant_context,
    migration_database_url,
    set_tenant_context,
    tenant_connection_scope,
)
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio(loop_scope="module")

TEST_DB = "sentinel_ai_test"
REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic.ini"
# Documentation reference for the guard this suite was written against; the
# fixtures always migrate the disposable database to the live head.
HEAD_REVISION = "0005_refresh_sessions"

# Tenant-owned, RLS-protected tables (must match revision 0003).
TENANT_TABLES = (
    "organizations",
    "users",
    "role_assignments",
    "applications",
    "provider_configs",
    "provider_credentials",
)
CATALOG_PROVIDER = "openai"

# Two organizations with *deliberately overlapping* resource identifiers: the
# same application name and the same provider/model pair, so isolation cannot
# rely on those values being globally unique.
ORG_A = "d1000000-0000-4000-8000-00000000000a"
ORG_B = "d1000000-0000-4000-8000-00000000000b"
USER_A = "d2000000-0000-4000-8000-00000000000a"
USER_B = "d2000000-0000-4000-8000-00000000000b"
ROLE_A = "d4000000-0000-4000-8000-00000000000a"
ROLE_B = "d4000000-0000-4000-8000-00000000000b"
APP_A = "d3000000-0000-4000-8000-00000000000a"
APP_B = "d3000000-0000-4000-8000-00000000000b"
CFG_A = "d5000000-0000-4000-8000-00000000000a"
CFG_B = "d5000000-0000-4000-8000-00000000000b"
CRED_A = "d6000000-0000-4000-8000-00000000000a"
CRED_B = "d6000000-0000-4000-8000-00000000000b"
SHARED_APP_NAME = "payments-worker"
SHARED_MODEL = "gpt-4o-mini"
# A syntactically valid UUID that is deliberately never inserted.
NEVER_ID = str(uuid.UUID(int=0))


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    return config


async def _alembic(url: str, revision: str) -> None:
    # env.py calls asyncio.run internally, so drive it in a worker thread that
    # has no running event loop of its own.
    await asyncio.to_thread(command.upgrade, _alembic_config(url), revision)


def _is_rls_denied(exc: BaseException) -> bool:
    """42501: privilege denial or a row-level-security WITH CHECK failure."""
    orig = getattr(exc, "orig", exc)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate == "42501":
        return True
    text_ = str(exc).lower()
    return "permission denied" in text_ or "row-level security" in text_


def _is_fk_violation(exc: BaseException) -> bool:
    """23503 / 23514: referential-integrity or check violation."""
    orig = getattr(exc, "orig", exc)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return sqlstate in {"23503", "23514"}


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def tenant_db() -> AsyncIterator[dict[str, str]]:
    """Create disposable sentinel_ai_test, migrate to head, seed two orgs.

    Yields the ``migrator_url`` (admin, BYPASSRLS) and ``app_url`` (the real
    non-owner API role the isolation assertions run as).
    """
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

    # Seed two organizations through the admin (BYPASSRLS) path — writing rows
    # for two tenants in one connection is exactly what the API role may never
    # do, so it must happen on the separate privileged admin connection.
    engine = create_async_engine(_render(test_migrator_url), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO provider_catalog_entries (id, provider, display_name, "
                    "supports_streaming, supports_chat, is_deterministic_mock, created_at) "
                    "VALUES (gen_random_uuid(), :provider, 'OpenAI', true, true, false, now())"
                ),
                {"provider": CATALOG_PROVIDER},
            )
            for org, user, role, app, cfg, cred in (
                (ORG_A, USER_A, ROLE_A, APP_A, CFG_A, CRED_A),
                (ORG_B, USER_B, ROLE_B, APP_B, CFG_B, CRED_B),
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
                        f"('{user}', '{org}', 'u{org[-1]}@iso.test', 'User', NULL, true, "
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
                        f"('{app}', '{org}', '{user}', '{SHARED_APP_NAME}', 'active', "
                        "now(), now())"
                    )
                )
                await conn.execute(
                    sa.text(
                        "INSERT INTO provider_configs (id, organization_id, provider, "
                        "model_name, application_id, is_enabled, created_at, updated_at) VALUES "
                        f"('{cfg}', '{org}', '{CATALOG_PROVIDER}', '{SHARED_MODEL}', '{app}', "
                        "true, now(), now())"
                    )
                )
                await conn.execute(
                    sa.text(
                        "INSERT INTO provider_credentials (id, organization_id, provider, "
                        "secret_ref, status, created_at) VALUES "
                        f"('{cred}', '{org}', '{CATALOG_PROVIDER}', "
                        f"'secret://env/{CATALOG_PROVIDER.upper()}_API_KEY', 'active', now())"
                    )
                )
    finally:
        await engine.dispose()

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


@asynccontextmanager
async def _ctx(app_url: str, organization_id: str | None) -> AsyncIterator[AsyncConnection]:
    """One short transaction as ``sentinel_app`` with ``organization_id`` context.

    ``organization_id=None`` models a missing/unauthenticated context. Committed
    on clean exit; any raised exception rolls the transaction back, so negative
    write attempts never persist.
    """
    engine = create_async_engine(app_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            if organization_id is not None:
                await set_tenant_context(conn, organization_id)
            yield conn
    finally:
        await engine.dispose()


async def _ids(app_url: str, organization_id: str | None, table: str) -> set[str]:
    async with _ctx(app_url, organization_id) as conn:
        rows = (await conn.execute(sa.text(f"SELECT id FROM {table}"))).scalars().all()
    return {str(r) for r in rows}


# ---------------------------------------------------------------------------
# Acceptance 1: no read path returns another organization's data
# ---------------------------------------------------------------------------


async def test_list_without_filter_never_returns_foreign_rows(
    tenant_db: dict[str, str],
) -> None:
    """Even with the tenant WHERE clause deliberately *removed*, RLS scopes reads.

    The step asks for a test that "deliberately removes an application filter
    and still expects RLS to block foreign rows": every query below is an
    unfiltered full-table scan, yet only the current organization's rows appear.
    """
    app_url = tenant_db["app_url"]
    own = {
        ORG_A: {APP_A, USER_A, ROLE_A, CFG_A, CRED_A},
        ORG_B: {APP_B, USER_B, ROLE_B, CFG_B, CRED_B},
    }[ORG_A]
    foreign = {APP_B, USER_B, ROLE_B, CFG_B, CRED_B}
    for table in (
        "users",
        "role_assignments",
        "applications",
        "provider_configs",
        "provider_credentials",
    ):
        visible = await _ids(app_url, ORG_A, table)
        assert visible, f"{table}: expected at least the org A row"
        assert visible <= {str(x) for x in own}, (table, visible)
        assert not (visible & foreign), f"{table}: leaked foreign rows {visible & foreign}"
    # organizations keys on its own id: only the caller's org is visible.
    assert await _ids(app_url, ORG_A, "organizations") == {ORG_A}


async def test_direct_object_reference_is_indistinguishable_from_missing(
    tenant_db: dict[str, str],
) -> None:
    """DOR (detail) access: a foreign id and a never-existing id look identical.

    Both return zero rows, so an attacker cannot probe which application ids
    exist in another tenant (existence leakage)."""
    app_url = tenant_db["app_url"]
    async with _ctx(app_url, ORG_A) as conn:
        foreign = (
            await conn.execute(
                sa.text("SELECT id, name FROM applications WHERE id = :i"), {"i": APP_B}
            )
        ).fetchall()
        missing = (
            await conn.execute(
                sa.text("SELECT id, name FROM applications WHERE id = :i"), {"i": NEVER_ID}
            )
        ).fetchall()
        own = (
            await conn.execute(
                sa.text("SELECT id, name FROM applications WHERE id = :i"), {"i": APP_A}
            )
        ).fetchall()
    assert foreign == [], "DOR exposed a foreign application"
    assert missing == [], "a fabricated id unexpectedly resolved"
    assert foreign == missing, "existence leakage: forbidden and not-found differ"
    assert len(own) == 1 and str(own[0][0]) == APP_A


async def test_overlapping_resource_names_do_not_cross_tenants(
    tenant_db: dict[str, str],
) -> None:
    """Both orgs own an application named the same; lookups stay tenant-scoped."""
    app_url = tenant_db["app_url"]
    for org, own_app, other_app in ((ORG_A, APP_A, APP_B), (ORG_B, APP_B, APP_A)):
        async with _ctx(app_url, org) as conn:
            rows = (
                (
                    await conn.execute(
                        sa.text("SELECT id FROM applications WHERE name = :n"),
                        {"n": SHARED_APP_NAME},
                    )
                )
                .scalars()
                .all()
            )
        assert {str(r) for r in rows} == {own_app}, f"{org}: name-based lookup leaked {other_app}"


# ---------------------------------------------------------------------------
# Acceptance 2: forged organization ids cannot override authenticated context
# ---------------------------------------------------------------------------


async def test_forged_organization_id_on_insert_is_rejected(tenant_db: dict[str, str]) -> None:
    """A write body claiming another organization is refused by RLS WITH CHECK.

    Even though ``sentinel_app`` can set arbitrary column values, the row's
    ``organization_id`` must equal the transaction context or the write fails."""
    app_url = tenant_db["app_url"]
    with pytest.raises(DBAPIError) as excinfo:
        async with _ctx(app_url, ORG_A) as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO applications (id, organization_id, owner_user_id, name, "
                    "status, created_at, updated_at) VALUES (gen_random_uuid(), "
                    f"'{ORG_B}', '{USER_B}', 'forged', 'active', now(), now())"
                )
            )
    assert _is_rls_denied(excinfo.value), str(excinfo.value)


async def test_forged_self_keyed_organization_insert_is_rejected(
    tenant_db: dict[str, str],
) -> None:
    """organizations keys on its own id, so forging another org's id is denied."""
    app_url = tenant_db["app_url"]
    with pytest.raises(DBAPIError) as excinfo:
        async with _ctx(app_url, ORG_A) as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO organizations (id, name, slug, is_active, "
                    "allow_self_approval, created_at, updated_at) VALUES "
                    f"('{ORG_B}', 'impostor', 'impostor-slug', true, false, now(), now())"
                )
            )
    assert _is_rls_denied(excinfo.value), str(excinfo.value)


async def test_handler_using_authenticated_context_succeeds_and_stays_private(
    tenant_db: dict[str, str],
) -> None:
    """The correct pattern: ignore the forged id, write under the auth context.

    A row inserted with the *context* organization is accepted and is invisible
    to the other tenant. Rolled back so counts stay deterministic."""
    app_url = tenant_db["app_url"]
    new_app = str(uuid.uuid4())
    engine = create_async_engine(app_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            await set_tenant_context(conn, ORG_A)
            result = await conn.execute(
                sa.text(
                    "INSERT INTO applications (id, organization_id, owner_user_id, name, "
                    "status, created_at, updated_at) VALUES (:id, :org, :owner, 'ok', "
                    "'active', now(), now())"
                ),
                {"id": new_app, "org": ORG_A, "owner": USER_A},
            )
            assert result.rowcount == 1
            await trans.rollback()
    finally:
        await engine.dispose()

    # And even had it committed, org B must never see an org A id.
    assert new_app not in await _ids(app_url, ORG_B, "applications")


# ---------------------------------------------------------------------------
# Acceptance 1/2: nested foreign-key references cannot cross tenants
# ---------------------------------------------------------------------------


async def test_nested_cross_tenant_application_reference_is_blocked(
    tenant_db: dict[str, str],
) -> None:
    """The nested IDOR the suite was written to catch, now closed by a guard.

    RLS hides foreign rows from *reads* but does not police a single-column
    foreign key, so a config in org A pointing at org B's application used to be
    accepted. Revision ``0004_nested_tenant_fk`` replaced that with a composite
    ``(application_id, organization_id) -> applications(id, organization_id)``
    key, so the mismatched pair now violates the foreign key."""
    app_url = tenant_db["app_url"]
    with pytest.raises(IntegrityError) as excinfo:
        async with _ctx(app_url, ORG_A) as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO provider_configs (id, organization_id, provider, model_name, "
                    "application_id, is_enabled, created_at, updated_at) VALUES "
                    f"(gen_random_uuid(), '{ORG_A}', '{CATALOG_PROVIDER}', 'nested-x', "
                    f"'{APP_B}', true, now(), now())"
                )
            )
    assert _is_fk_violation(excinfo.value), str(excinfo.value)
    assert "fk_provider_configs_application_org_applications" in str(excinfo.value)


async def test_nested_join_resolves_only_within_the_tenant(
    tenant_db: dict[str, str],
) -> None:
    """A config JOINed to its application never surfaces a foreign application.

    RLS applies to every table in the query, so even a permissive join is bound
    to the caller's organization on both sides."""
    app_url = tenant_db["app_url"]
    for org, own_cfg, own_app in ((ORG_A, CFG_A, APP_A), (ORG_B, CFG_B, APP_B)):
        async with _ctx(app_url, org) as conn:
            rows = (
                await conn.execute(
                    sa.text(
                        "SELECT pc.id, a.id FROM provider_configs pc "
                        "JOIN applications a ON pc.application_id = a.id"
                    )
                )
            ).fetchall()
        assert {(str(c), str(a)) for c, a in rows} == {(own_cfg, own_app)}, (org, rows)


# ---------------------------------------------------------------------------
# Acceptance 3: missing context and pooled reuse are covered
# ---------------------------------------------------------------------------


async def test_missing_context_denies_reads_and_writes(tenant_db: dict[str, str]) -> None:
    """No tenant context must mean no rows and no writes — never everything."""
    app_url = tenant_db["app_url"]
    async with _ctx(app_url, None) as conn:  # deliberately no SET of context
        for table in TENANT_TABLES:
            count = (await conn.execute(sa.text(f"SELECT count(*) FROM {table}"))).scalar()
            assert count == 0, f"{table} leaked {count} rows without tenant context"
        with pytest.raises(DBAPIError) as excinfo:
            await conn.execute(
                sa.text(
                    "INSERT INTO applications (id, organization_id, owner_user_id, name, "
                    "status, created_at, updated_at) VALUES (gen_random_uuid(), "
                    f"'{ORG_A}', '{USER_A}', 'orphan', 'active', now(), now())"
                )
            )
        assert _is_rls_denied(excinfo.value), str(excinfo.value)


async def test_pooled_alternating_tenant_requests_do_not_leak(tenant_db: dict[str, str]) -> None:
    """A single physical connection reused across alternating tenants.

    ``pool_size=1`` forces the same backend connection back into service. Because
    the context is transaction-local, it is discarded at every commit: the read
    on a freshly reused, context-less connection sees nothing, and organization B
    never inherits organization A's context (or vice-versa)."""
    app_url = tenant_db["app_url"]
    engine = create_async_engine(app_url, poolclass=None, pool_size=1, max_overflow=0)
    try:
        sequence = (
            (ORG_A, APP_A, APP_B),
            (ORG_B, APP_B, APP_A),
            (ORG_A, APP_A, APP_B),
            (ORG_B, APP_B, APP_A),
        )
        for org, own, other in sequence:
            async with tenant_connection_scope(engine, org) as conn:
                ids = {
                    str(i)
                    for i in (await conn.execute(sa.text("SELECT id FROM applications"))).scalars()
                }
            assert own in ids and other not in ids, (org, ids)
            # Same pooled connection, brand-new transaction: context is gone.
            async with engine.begin() as conn:
                leaked = (await conn.execute(sa.text("SELECT count(*) FROM applications"))).scalar()
                assert leaked == 0, "pooled connection carried a tenant context across requests"
                assert await current_tenant_context(conn) is None
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Global catalog is shared, not tenant-filtered; the API role cannot edit it
# ---------------------------------------------------------------------------


async def test_shared_catalog_visible_to_every_tenant_and_read_only_for_app(
    tenant_db: dict[str, str],
) -> None:
    """Reference data is global (visible under any context) but immutable to the
    API role — tenant isolation must not accidentally hide the provider catalog,
    and least-privilege grants (Day 009) must still deny catalog writes."""
    app_url = tenant_db["app_url"]
    for org in (ORG_A, ORG_B):
        async with _ctx(app_url, org) as conn:
            providers = set(
                (await conn.execute(sa.text("SELECT provider FROM provider_catalog_entries")))
                .scalars()
                .all()
            )
        assert CATALOG_PROVIDER in providers, f"catalog hidden from {org}"
    with pytest.raises(DBAPIError) as excinfo:
        async with _ctx(app_url, ORG_A) as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO provider_catalog_entries (id, provider, display_name, "
                    "supports_streaming, supports_chat, is_deterministic_mock, created_at) "
                    "VALUES (gen_random_uuid(), 'rogue', 'Rogue', false, false, false, now())"
                )
            )
    assert _is_rls_denied(excinfo.value), str(excinfo.value)


# ---------------------------------------------------------------------------
# HTTP surface: only reviewed, principal-scoped tenant endpoints may exist
# ---------------------------------------------------------------------------


async def test_current_http_surface_exposes_no_unscoped_tenant_endpoints(
    shell_app: Any,
) -> None:
    """Lock the invariant at the edge as scoped tenant endpoints land (Day 012+).

    Tenant data is only reachable through the RLS-scoped repository/store layer
    proven above. This guards the HTTP surface so that adding a tenant-resource
    route *without* a tenant context fails the security gate on purpose: every
    path below must be an explicit, reviewed allow-list entry, and every
    tenant-data route must derive its organization from the authenticated
    bearer principal — never from client-supplied input.

    Paths are read from the OpenAPI schema, not ``app.routes``: routers are
    included lazily in this FastAPI version, so only the generated schema is a
    faithful, flattened view of the exposed surface.
    """
    tenant_resource_words = (
        "application",
        "organization",
        "user",
        "role",
        "provider",
        "credential",
        "audit",
    )
    # Day 012 auth surface + Day 013 application / scoped API-key / audit-trail
    # routes. Every one is JWT-authenticated, RBAC-guarded (ADR-0004) and
    # hard-scoped to ``principal.organization_id`` with RLS behind it; foreign
    # ids answer the indistinguishable 404, so no cross-tenant read is possible.
    allowed = {
        "/api/v1/version",
        "/api/v1/echo",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/applications",
        "/api/v1/applications/{application_id}",
        "/api/v1/applications/{application_id}/api-keys",
        "/api/v1/applications/{application_id}/api-keys/{key_id}/revoke",
        "/api/v1/applications/{application_id}/api-keys/{key_id}/rotate",
        "/api/v1/audit-logs",
    }
    paths = set(shell_app.openapi()["paths"])
    offenders = {
        path
        for path in paths
        if path.startswith("/api/")
        and path not in allowed
        and any(word in path.lower() for word in tenant_resource_words)
    }
    assert not offenders, f"unscoped tenant-data endpoint(s) exposed: {sorted(offenders)}"
    api_paths = {path for path in paths if path.startswith("/api/")}
    assert api_paths <= allowed, api_paths

    # Every tenant-data route must sit behind the bearer scheme, so the
    # organization can only come from the verified token. The stateless shell
    # helpers and the identity-establishment auth routes are the reviewed
    # exceptions; /me and all Day 013 resource routes require the bearer.
    bearer_exempt = {
        "/api/v1/version",
        "/api/v1/echo",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
    }
    operations = shell_app.openapi()["paths"]
    for path, item in operations.items():
        if not path.startswith("/api/") or path in bearer_exempt:
            continue
        for operation in item.values():
            assert any("HTTPBearer" in scheme for scheme in operation.get("security", [])), (
                f"{path} exposes an operation without bearer security"
            )
