"""Day 013 integration tests: the append-only audit trail on real PostgreSQL.

Proves the two acceptance-checklist facts that only the database can settle:

* **Immutability is a grant, not a promise** — revision ``0006`` gives the
  runtime ``sentinel_app`` role ``SELECT`` + ``INSERT`` on ``audit_logs`` and
  *nothing else*, under forced RLS. UPDATE and DELETE fail with 42501/RLS
  denials even for the tenant's own rows.
* **Audit rides the mutation's transaction** — a rolled-back mutation leaves
  neither change nor trail row; a committed one leaves both, atomically.

Also locks the ``detail_not_raw_key`` CHECK: no plaintext ``sk_live_`` secret
can be written into the trail, even by a bug that bypasses Python.

Runs against the disposable ``sentinel_ai_test`` database, migrated to head,
exactly like the Day 010-012 suites; the development database is untouched.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.auth.keys import API_KEY_PREFIX
from app.infrastructure.database import (
    application_database_url,
    create_session_factory,
    migration_database_url,
    set_tenant_context,
    tenant_session_scope,
)
from app.models.application import Application
from app.models.audit import AuditActorType
from app.services.applications import PostgresApplicationStore
from app.services.audit import AuditAction, AuditResource, append_audit
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio(loop_scope="module")

TEST_DB = "sentinel_ai_test"
REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic.ini"

ORG_A = uuid.UUID("f6000000-0000-4000-8000-00000000000a")
ORG_B = uuid.UUID("f6000000-0000-4000-8000-00000000000b")
USER_A = uuid.UUID("f7000000-0000-4000-8000-00000000000a")
USER_B = uuid.UUID("f7000000-0000-4000-8000-00000000000b")


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    return config


async def _alembic(url: str, revision: str) -> None:
    await asyncio.to_thread(command.upgrade, _alembic_config(url), revision)


def _is_permission_denied(exc: BaseException) -> bool:
    """42501 grant/RLS failures surface in a few shapes; accept all of them."""
    orig = getattr(exc, "orig", exc)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate == "42501":
        return True
    text_ = str(exc).lower()
    return "permission denied" in text_ or "row-level security" in text_


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def audit_db() -> AsyncIterator[dict[str, str]]:
    """Create sentinel_ai_test, migrate to head, seed two tenants with users."""
    migrator_url = make_url(migration_database_url())
    app_url = make_url(application_database_url())

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

    engine = create_async_engine(_render(test_migrator_url), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for org, user, email in (
                (ORG_A, USER_A, "alice@audit.test"),
                (ORG_B, USER_B, "bob@audit.test"),
            ):
                await conn.execute(
                    sa.text(
                        "INSERT INTO organizations (id, name, slug, is_active, "
                        "allow_self_approval, created_at, updated_at) VALUES "
                        "(:org, :name, :slug, true, false, now(), now())"
                    ),
                    {"org": org, "name": f"Org {email}", "slug": f"audit-{str(org)[-1]}"},
                )
                await conn.execute(
                    sa.text(
                        "INSERT INTO users (id, organization_id, email, display_name, "
                        "password_hash, is_active, created_at, updated_at) VALUES "
                        "(:user, :org, :email, 'User', "
                        "'$argon2id$placeholder-not-used-for-login', true, now(), now())"
                    ),
                    {"user": user, "org": org, "email": email},
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
async def _app_conn(
    app_url: str, organization_id: uuid.UUID | None = None
) -> AsyncIterator[AsyncConnection]:
    """One ``sentinel_app`` transaction, optionally bound to a tenant context."""
    engine = create_async_engine(app_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            if organization_id is not None:
                await set_tenant_context(conn, organization_id)
            yield conn
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def store(audit_db: dict[str, str]) -> AsyncIterator[PostgresApplicationStore]:
    """Production store as ``sentinel_app``; migrator wipes the tables after."""
    engine = create_async_engine(audit_db["app_url"], poolclass=NullPool)
    try:
        yield PostgresApplicationStore(create_session_factory(engine))
    finally:
        await engine.dispose()
        migrator = create_async_engine(
            audit_db["migrator_url"], isolation_level="AUTOCOMMIT", poolclass=NullPool
        )
        async with migrator.begin() as conn:
            await conn.execute(sa.text("DELETE FROM audit_logs"))
            await conn.execute(sa.text("DELETE FROM api_keys"))
            await conn.execute(sa.text("DELETE FROM applications"))
        await migrator.dispose()


async def _create_application(s: PostgresApplicationStore, name: str) -> uuid.UUID:
    view = await s.create_application(
        organization_id=ORG_A,
        actor_user_id=USER_A,
        name=name,
        description=None,
        request_id=f"req-{name}",
    )
    return view.id


# ---------------------------------------------------------------------------
# Immutability: SELECT + INSERT are the whole grant
# ---------------------------------------------------------------------------


async def test_audit_rows_cannot_be_updated_or_deleted_by_api_role(
    store: PostgresApplicationStore, audit_db: dict[str, str]
) -> None:
    """The checklist's core DB fact: the application role cannot rewrite or
    erase history — not even its own tenant's rows."""
    resource_id = await _create_application(store, "immutable-app")

    with pytest.raises(DBAPIError) as excinfo:
        async with _app_conn(audit_db["app_url"], ORG_A) as conn:
            await conn.execute(
                sa.text(
                    "UPDATE audit_logs SET action = 'application.forged' "
                    "WHERE resource_id = :resource_id"
                ),
                {"resource_id": resource_id},
            )
    assert _is_permission_denied(excinfo.value), str(excinfo.value)

    with pytest.raises(DBAPIError) as excinfo:
        async with _app_conn(audit_db["app_url"], ORG_A) as conn:
            await conn.execute(
                sa.text("DELETE FROM audit_logs WHERE resource_id = :resource_id"),
                {"resource_id": resource_id},
            )
    assert _is_permission_denied(excinfo.value), str(excinfo.value)

    # And without a tenant context at all, even SELECT is blocked by forced RLS.
    async with _app_conn(audit_db["app_url"]) as conn:
        visible = (await conn.execute(sa.text("SELECT count(*) FROM audit_logs"))).scalar_one()
    assert visible == 0


async def test_audit_insert_and_select_work_in_tenant_context(
    store: PostgresApplicationStore, audit_db: dict[str, str]
) -> None:
    resource_id = await _create_application(store, "readable-app")
    async with _app_conn(audit_db["app_url"], ORG_A) as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO audit_logs (id, organization_id, actor_type, actor_id, action, "
                "resource_type, resource_id, detail, request_id, created_at) VALUES "
                "(:id, :org, 'user', :actor, 'integration.probe', 'application', :resource_id, "
                ":detail, 'req-probe', now())"
            ),
            {
                "id": uuid.uuid4(),
                "org": ORG_A,
                "actor": USER_A,
                "resource_id": resource_id,
                "detail": '{"probe": true}',
            },
        )
        actions = (
            (
                await conn.execute(
                    sa.text("SELECT action FROM audit_logs WHERE resource_id = :resource_id"),
                    {"resource_id": resource_id},
                )
            )
            .scalars()
            .all()
        )
    assert "integration.probe" in actions
    assert "application.created" in actions  # the store's own transactional write


# ---------------------------------------------------------------------------
# No plaintext secret can enter the trail, even through a Python bug
# ---------------------------------------------------------------------------


async def test_detail_check_rejects_raw_key_payloads(
    store: PostgresApplicationStore, audit_db: dict[str, str]
) -> None:
    resource_id = await _create_application(store, "secret-check-app")
    with pytest.raises(DBAPIError) as excinfo:
        async with _app_conn(audit_db["app_url"], ORG_A) as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO audit_logs (id, organization_id, actor_type, action, "
                    "resource_type, resource_id, detail, created_at) VALUES "
                    "(:id, :org, 'user', 'api_key.created', 'api_key', :resource_id, "
                    ":detail, now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "org": ORG_A,
                    "resource_id": resource_id,
                    "detail": f'{{"leak": "{API_KEY_PREFIX}0123456789abcdef0123456789abcdef"}}',
                },
            )
    assert "check constraint" in str(excinfo.value).lower() or "detail_not_raw_key" in str(
        excinfo.value
    ), str(excinfo.value)


async def test_python_serializer_refuses_secret_bearing_details() -> None:
    """The writer layer rejects before the database is even involved.

    Day 014 widened the rule from "no raw API key" to "no raw API key and no
    sealed payload": once prompt bodies are encrypted at rest, a ciphertext
    blob is just as much secret material as ``sk_live_``.
    """
    from app.services.audit import FERNET_TOKEN_MARKER, SEALED_PAYLOAD_MARKER, serialize_detail

    guard = "secret or sealed-payload material"
    with pytest.raises(ValueError, match=guard):
        serialize_detail({"key": f"{API_KEY_PREFIX}aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"})
    # Nested structures are scanned recursively, not just top-level strings.
    with pytest.raises(ValueError, match=guard):
        serialize_detail({"outer": {"inner": [f"prefix-{API_KEY_PREFIX}bbbb"]}})
    with pytest.raises(ValueError, match=guard):
        serialize_detail({"body": f"{SEALED_PAYLOAD_MARKER}v1:{FERNET_TOKEN_MARKER}abcdef"})
    # Benign metadata serializes with stable, canonical (compact) ordering.
    assert serialize_detail({"b": 1, "a": "x"}) == '{"a":"x","b":1}'
    # A content hash is hex-only and stays writable next to a sealed-body marker.
    assert serialize_detail({"content_hash": "a" * 64}) == '{"content_hash":"' + "a" * 64 + '"}'


# ---------------------------------------------------------------------------
# Transactional atomicity: change and trail commit or vanish together
# ---------------------------------------------------------------------------


async def test_rolled_back_mutation_leaves_no_audit_row(audit_db: dict[str, str]) -> None:
    """A failure anywhere after ``append_audit`` unwinds both writes together."""
    factory = create_session_factory(create_async_engine(audit_db["app_url"], poolclass=NullPool))

    class Rollback(Exception):
        pass

    resource_id = uuid.uuid4()
    with pytest.raises(Rollback):
        async with tenant_session_scope(factory, ORG_A) as session:
            session.add(
                Application(
                    id=resource_id,
                    organization_id=ORG_A,
                    owner_user_id=USER_A,
                    name="never-committed",
                    status="active",
                )
            )
            await session.flush()
            append_audit(
                session,
                organization_id=ORG_A,
                actor_type=AuditActorType.USER,
                actor_id=USER_A,
                action=AuditAction.APPLICATION_CREATED,
                resource_type=AuditResource.APPLICATION,
                resource_id=resource_id,
                detail={"name": "never-committed"},
                request_id="req-rollback",
            )
            raise Rollback  # tenant_session_scope must roll the tx back

    migrator = create_async_engine(
        audit_db["migrator_url"], isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    try:
        async with migrator.connect() as conn:
            apps = (
                await conn.execute(
                    sa.text("SELECT count(*) FROM applications WHERE id = :id"),
                    {"id": resource_id},
                )
            ).scalar_one()
            trails = (
                await conn.execute(
                    sa.text("SELECT count(*) FROM audit_logs WHERE resource_id = :id"),
                    {"id": resource_id},
                )
            ).scalar_one()
    finally:
        await migrator.dispose()
    assert apps == 0
    assert trails == 0


async def test_committed_mutation_and_audit_land_together(
    store: PostgresApplicationStore, audit_db: dict[str, str]
) -> None:
    resource_id = await _create_application(store, "atomic-app")
    async with _app_conn(audit_db["app_url"], ORG_A) as conn:
        app_count = (
            await conn.execute(
                sa.text("SELECT count(*) FROM applications WHERE id = :id"),
                {"id": resource_id},
            )
        ).scalar_one()
        trail = (
            (
                await conn.execute(
                    sa.text("SELECT action, request_id FROM audit_logs WHERE resource_id = :id"),
                    {"id": resource_id},
                )
            )
            .mappings()
            .all()
        )
    assert app_count == 1
    assert [record["action"] for record in trail] == ["application.created"]
    assert trail[0]["request_id"] == "req-atomic-app"


# ---------------------------------------------------------------------------
# RLS isolation: the trail never leaks across tenants
# ---------------------------------------------------------------------------


async def test_trail_is_invisible_and_unwritable_across_tenants(
    store: PostgresApplicationStore, audit_db: dict[str, str]
) -> None:
    resource_id = await _create_application(store, "private-trail-app")

    # B's context sees none of A's trail...
    async with _app_conn(audit_db["app_url"], ORG_B) as conn:
        rows = (
            await conn.execute(
                sa.text("SELECT count(*) FROM audit_logs WHERE resource_id = :id"),
                {"id": resource_id},
            )
        ).scalar_one()
    assert rows == 0

    # ...and nothing B writes can land in A's tenant: the WITH CHECK pins every
    # row to the session's own context, so writes only ever extend B's trail.
    async with _app_conn(audit_db["app_url"], ORG_B) as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO audit_logs (id, organization_id, actor_type, action, "
                "resource_type, resource_id, created_at) VALUES "
                "(:id, :org_b, 'user', 'integration.probe', 'application', :resource_id, now())"
            ),
            {"id": uuid.uuid4(), "org_b": ORG_B, "resource_id": resource_id},
        )
    async with _app_conn(audit_db["app_url"], ORG_A) as conn:
        a_still_private = (
            await conn.execute(
                sa.text("SELECT count(*) FROM audit_logs WHERE resource_id = :id"),
                {"id": resource_id},
            )
        ).scalar_one()
    async with _app_conn(audit_db["app_url"], ORG_B) as conn:
        b_own_rows = (
            await conn.execute(
                sa.text("SELECT count(*) FROM audit_logs WHERE action = 'integration.probe'")
            )
        ).scalar_one()
    assert b_own_rows == 1
    assert a_still_private == 1  # only A's own application.created entry

    # A context writing a B-tenant row is rejected by the RLS WITH CHECK.
    app_b = await store.create_application(
        organization_id=ORG_B,
        actor_user_id=USER_B,
        name="b-owned-app",
        description=None,
        request_id="req-b",
    )
    with pytest.raises(DBAPIError) as excinfo:
        async with _app_conn(audit_db["app_url"], ORG_A) as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO audit_logs (id, organization_id, actor_type, action, "
                    "resource_type, resource_id, created_at) VALUES "
                    "(:id, :org_b, 'user', 'application.created', 'application', :app_b, now())"
                ),
                {"id": uuid.uuid4(), "org_b": ORG_B, "app_b": app_b.id},
            )
    assert _is_permission_denied(excinfo.value), str(excinfo.value)
