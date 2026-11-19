"""Day 013 security tests: application API keys against real PostgreSQL.

These run the production :class:`~app.services.applications.PostgresApplicationStore`
and :class:`~app.auth.api_keys.ApiKeyDirectory`, plus the revision ``0006``
database objects, exactly as shipped, as the least-privilege API role
``sentinel_app`` (``NOBYPASSRLS``). They prove the parts a fake store cannot:
the context-free ``SECURITY DEFINER`` key lookup, hashed-at-rest credentials
whose raw value never lands in a column, RLS isolation of ``api_keys``, the
composite cross-tenant FK rejections, the no-DELETE grant, and the accepted
usability rules (expired/revoked/foreign keys resolve to nothing).

Requires the local Docker core stack (same as the Day 010-012 suites); a
disposable ``sentinel_ai_test`` is created, migrated and dropped around the
module so the development database is never touched.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.auth.api_keys import ApiKeyDirectory
from app.auth.keys import API_KEY_PREFIX, KeyState, is_api_key_shape
from app.auth.security import token_digest
from app.core.errors import AppError
from app.core.time import utcnow
from app.infrastructure.database import (
    application_database_url,
    create_session_factory,
    migration_database_url,
    set_tenant_context,
)
from app.services.applications import (
    ApplicationNotActiveError,
    ApplicationView,
    IssuedApiKey,
    KeyNotRotatableError,
    NotFoundError,
    PostgresApplicationStore,
)
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio(loop_scope="module")

TEST_DB = "sentinel_ai_test"
REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic.ini"

ORG_A = uuid.UUID("f1000000-0000-4000-8000-00000000000a")
ORG_B = uuid.UUID("f1000000-0000-4000-8000-00000000000b")
USER_A = uuid.UUID("f2000000-0000-4000-8000-00000000000a")
USER_B = uuid.UUID("f2000000-0000-4000-8000-00000000000b")
ROLE_A = uuid.UUID("f4000000-0000-4000-8000-00000000000a")
ROLE_B = uuid.UUID("f4000000-0000-4000-8000-00000000000b")


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    return config


async def _alembic(url: str, revision: str) -> None:
    # env.py calls asyncio.run internally, so run in a thread with no loop.
    await asyncio.to_thread(command.upgrade, _alembic_config(url), revision)


def _is_permission_denied(exc: BaseException) -> bool:
    orig = getattr(exc, "orig", exc)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate == "42501":
        return True
    text_ = str(exc).lower()
    return "permission denied" in text_ or "row-level security" in text_


def _is_fk_violation(exc: BaseException) -> bool:
    orig = getattr(exc, "orig", exc)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return sqlstate == "23503" or "foreign key" in str(exc).lower()


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def keys_db() -> AsyncIterator[dict[str, str]]:
    """Create sentinel_ai_test, migrate to head, seed two owner tenants."""
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
            for org, user, role, email in (
                (ORG_A, USER_A, ROLE_A, "alice@keys.test"),
                (ORG_B, USER_B, ROLE_B, "bob@keys.test"),
            ):
                await conn.execute(
                    sa.text(
                        "INSERT INTO organizations (id, name, slug, is_active, "
                        "allow_self_approval, created_at, updated_at) VALUES "
                        "(:org, :name, :slug, true, false, now(), now())"
                    ),
                    {"org": org, "name": f"Org {email}", "slug": f"keys-{str(org)[-1]}"},
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
                await conn.execute(
                    sa.text(
                        "INSERT INTO role_assignments (id, user_id, organization_id, role, "
                        "created_at) VALUES (:role, :user, :org, 'owner', now())"
                    ),
                    {"role": role, "user": user, "org": org},
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


@pytest_asyncio.fixture(loop_scope="function")
async def env(keys_db: dict[str, str]) -> AsyncIterator[dict[str, object]]:
    """Store + directory as ``sentinel_app``, plus raw-connection URLs.

    Teardown clears the Day 013 tables through the privileged migrator role
    (the API role deliberately holds no DELETE on ``api_keys`` and no
    UPDATE/DELETE on ``audit_logs``), so each test starts from an empty surface.
    """
    engine = create_async_engine(keys_db["app_url"], poolclass=NullPool)
    factory = create_session_factory(engine)
    try:
        yield {
            "store": PostgresApplicationStore(factory),
            "directory": ApiKeyDirectory(factory),
            "app_url": keys_db["app_url"],
            "migrator_url": keys_db["migrator_url"],
        }
    finally:
        await engine.dispose()
        migrator = create_async_engine(
            keys_db["migrator_url"], isolation_level="AUTOCOMMIT", poolclass=NullPool
        )
        async with migrator.begin() as conn:
            await conn.execute(sa.text("DELETE FROM audit_logs"))
            await conn.execute(sa.text("DELETE FROM api_keys"))
            await conn.execute(sa.text("DELETE FROM applications"))
        await migrator.dispose()


async def _make_application(
    env: dict[str, object], *, org: uuid.UUID, user: uuid.UUID, name: str
) -> ApplicationView:
    store = env["store"]
    assert isinstance(store, PostgresApplicationStore)
    return await store.create_application(
        organization_id=org,
        actor_user_id=user,
        name=name,
        description=None,
        request_id="req-seed",
    )


async def _issue_key(
    env: dict[str, object],
    *,
    org: uuid.UUID,
    user: uuid.UUID,
    name: str = "ci-key",
    scopes: list[str] | None = None,
    expires_in_days: int | None = None,
) -> tuple[ApplicationView, IssuedApiKey]:
    store = env["store"]
    assert isinstance(store, PostgresApplicationStore)
    app = await _make_application(env, org=org, user=user, name=f"app-{uuid.uuid4().hex[:10]}")
    issued = await store.create_api_key(
        organization_id=org,
        application_id=app.id,
        actor_user_id=user,
        name=name,
        scopes=scopes or ["gateway:invoke"],
        expires_in_days=expires_in_days,
        request_id="req-issue",
    )
    return app, issued


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


# ---------------------------------------------------------------------------
# Show-once storage: raw keys never rest in any column
# ---------------------------------------------------------------------------


async def test_issued_key_is_hashed_at_rest_and_raw_never_stored(env: dict[str, object]) -> None:
    app, issued = await _issue_key(env, org=ORG_A, user=USER_A)
    raw = issued.secret
    assert is_api_key_shape(raw)
    assert issued.view.key_prefix == raw[: len(API_KEY_PREFIX) + 12]

    async with _app_conn(str(env["app_url"]), ORG_A) as conn:
        row = (
            await conn.execute(
                sa.text(
                    "SELECT secret_hash, key_prefix, name, scopes FROM api_keys WHERE id = :id"
                ),
                {"id": issued.view.id},
            )
        ).one()
    assert row.secret_hash == token_digest(raw)
    assert len(row.secret_hash) == 64 and row.secret_hash != raw
    assert raw not in "".join(str(value) for value in tuple(row))
    # The audit detail carries metadata only — not even the display prefix.
    async with _app_conn(str(env["app_url"]), ORG_A) as conn:
        details = (
            (
                await conn.execute(
                    sa.text(
                        "SELECT action, detail FROM audit_logs WHERE resource_id = :resource_id"
                    ),
                    {"resource_id": issued.view.id},
                )
            )
            .mappings()
            .all()
        )
    assert [record["action"] for record in details] == ["api_key.created"]
    assert raw not in str(details[0]["detail"])
    assert API_KEY_PREFIX not in str(details[0]["detail"])
    # And the create itself audited the application inside the same tenant.
    async with _app_conn(str(env["app_url"]), ORG_A) as conn:
        app_actions = (
            (
                await conn.execute(
                    sa.text("SELECT action FROM audit_logs WHERE resource_id = :resource_id"),
                    {"resource_id": app.id},
                )
            )
            .scalars()
            .all()
        )
    assert app_actions == ["application.created"]


# ---------------------------------------------------------------------------
# Resolution: the definer lookup learns the tenant from the stored record
# ---------------------------------------------------------------------------


async def test_definer_lookup_resolves_organization_from_stored_record(
    env: dict[str, object],
) -> None:
    app, issued = await _issue_key(env, org=ORG_A, user=USER_A)
    directory = env["directory"]
    assert isinstance(directory, ApiKeyDirectory)

    # No tenant context exists at lookup time — the digest is the only input.
    identity = await directory.lookup(issued.secret)
    assert identity is not None
    assert identity.organization_id == ORG_A
    assert identity.application_id == app.id
    assert identity.scopes == "gateway:invoke"

    now = utcnow()
    usable = await directory.resolve_usable(issued.secret, now=now)
    assert usable is not None and usable.key_id == issued.view.id


@pytest.mark.parametrize(
    "presented",
    [
        "sk_live_" + "0" * 32,  # right shape, never issued
        token_digest("sk_live_" + "0" * 32),  # a digest is not a raw key
        "wrong-prefix-abcdef",
        "",
    ],
)
async def test_unknown_or_malformed_keys_resolve_to_nothing(
    env: dict[str, object], presented: str
) -> None:
    directory = env["directory"]
    assert isinstance(directory, ApiKeyDirectory)
    assert await directory.lookup(presented) is None
    assert await directory.resolve_usable(presented, now=utcnow()) is None


async def test_expiry_boundary_denies_access(env: dict[str, object]) -> None:
    app, issued = await _issue_key(env, org=ORG_A, user=USER_A, expires_in_days=1)
    directory = env["directory"]
    assert isinstance(directory, ApiKeyDirectory)
    assert issued.view.expires_at is not None

    before = await directory.resolve_usable(issued.secret, now=utcnow())
    assert before is not None
    after = await directory.resolve_usable(
        issued.secret, now=issued.view.expires_at + timedelta(seconds=1)
    )
    assert after is None


async def test_revocation_is_immediate_and_row_survives(env: dict[str, object]) -> None:
    app, issued = await _issue_key(env, org=ORG_A, user=USER_A)
    directory = env["directory"]
    store = env["store"]
    assert isinstance(directory, ApiKeyDirectory)
    assert isinstance(store, PostgresApplicationStore)

    assert await directory.resolve_usable(issued.secret, now=utcnow()) is not None
    view = await store.revoke_api_key(
        organization_id=ORG_A,
        application_id=app.id,
        key_id=issued.view.id,
        actor_user_id=USER_A,
        request_id="req-revoke",
    )
    assert view.state is KeyState.REVOKED and view.revoked_at is not None
    # Immediate: usable no more on the very next check, at any clock.
    assert await directory.resolve_usable(issued.secret, now=utcnow()) is None

    # The row survives as evidence: the API role holds no DELETE on api_keys.
    with pytest.raises(DBAPIError) as excinfo:
        async with _app_conn(str(env["app_url"]), ORG_A) as conn:
            await conn.execute(
                sa.text("DELETE FROM api_keys WHERE id = :id"), {"id": issued.view.id}
            )
    assert _is_permission_denied(excinfo.value), str(excinfo.value)
    async with _app_conn(str(env["app_url"]), ORG_A) as conn:
        surviving = (
            await conn.execute(
                sa.text("SELECT count(*) FROM api_keys WHERE id = :id"), {"id": issued.view.id}
            )
        ).scalar_one()
    assert surviving == 1


# ---------------------------------------------------------------------------
# Rotation: deliberate, bounded overlap — and revoked keys never rotate
# ---------------------------------------------------------------------------


async def test_rotation_overlap_then_hard_expiry(env: dict[str, object]) -> None:
    app, issued = await _issue_key(env, org=ORG_A, user=USER_A)
    store = env["store"]
    directory = env["directory"]
    assert isinstance(store, PostgresApplicationStore)
    assert isinstance(directory, ApiKeyDirectory)

    successor, predecessor = await store.rotate_api_key(
        organization_id=ORG_A,
        application_id=app.id,
        key_id=issued.view.id,
        actor_user_id=USER_A,
        grace_seconds=60,
        request_id="req-rotate",
    )
    assert predecessor.superseded_by_id == successor.view.id
    assert predecessor.expires_at is not None
    overlap_end = predecessor.expires_at

    # Inside the overlap both keys work; past it the predecessor is dead while
    # the successor lives on. The successor never expires on its own.
    inside = overlap_end - timedelta(seconds=30)
    outside = overlap_end + timedelta(seconds=1)
    assert await directory.resolve_usable(issued.secret, now=inside) is not None
    assert await directory.resolve_usable(successor.secret, now=inside) is not None
    assert await directory.resolve_usable(issued.secret, now=outside) is None
    assert await directory.resolve_usable(successor.secret, now=outside) is not None
    assert successor.view.expires_at is None

    # Grace can only shorten life: a 30-day key rotates into a 60-second tail.
    listing = await store.list_api_keys(
        organization_id=ORG_A, application_id=app.id, cursor=None, limit=20
    )
    rotated = next(item for item in listing.items if item.id == issued.view.id)
    assert rotated.state is KeyState.ACTIVE  # still within overlap at "now"
    assert rotated.expires_at is not None
    assert rotated.expires_at < utcnow() + timedelta(days=1)


async def test_revoked_keys_cannot_be_rotated(env: dict[str, object]) -> None:
    app, issued = await _issue_key(env, org=ORG_A, user=USER_A)
    store = env["store"]
    assert isinstance(store, PostgresApplicationStore)
    await store.revoke_api_key(
        organization_id=ORG_A,
        application_id=app.id,
        key_id=issued.view.id,
        actor_user_id=USER_A,
        request_id="req-revoke",
    )
    with pytest.raises(KeyNotRotatableError):
        await store.rotate_api_key(
            organization_id=ORG_A,
            application_id=app.id,
            key_id=issued.view.id,
            actor_user_id=USER_A,
            grace_seconds=60,
            request_id="req-rotate",
        )


async def test_archived_application_cannot_receive_keys(env: dict[str, object]) -> None:
    app = await _make_application(env, org=ORG_A, user=USER_A, name="old-app")
    store = env["store"]
    assert isinstance(store, PostgresApplicationStore)
    await store.update_application(
        organization_id=ORG_A,
        application_id=app.id,
        actor_user_id=USER_A,
        status="archived",
        request_id="req-archive",
    )
    with pytest.raises(ApplicationNotActiveError):
        await store.create_api_key(
            organization_id=ORG_A,
            application_id=app.id,
            actor_user_id=USER_A,
            name="late",
            scopes=["gateway:invoke"],
            expires_in_days=None,
            request_id="req-key",
        )


# ---------------------------------------------------------------------------
# Cross-tenant: foreign rows are invisible, foreign references are rejected
# ---------------------------------------------------------------------------


async def test_foreign_tenant_cannot_see_or_touch_keys(env: dict[str, object]) -> None:
    app, issued = await _issue_key(env, org=ORG_A, user=USER_A)
    store = env["store"]
    assert isinstance(store, PostgresApplicationStore)

    # B cannot even see A's application, so every nested route 404s identically.
    with pytest.raises(NotFoundError):
        await store.get_application(organization_id=ORG_B, application_id=app.id)
    with pytest.raises(NotFoundError):
        await store.revoke_api_key(
            organization_id=ORG_B,
            application_id=app.id,
            key_id=issued.view.id,
            actor_user_id=USER_B,
            request_id="req-evil",
        )
    with pytest.raises(NotFoundError):
        await store.list_api_keys(
            organization_id=ORG_B, application_id=app.id, cursor=None, limit=20
        )
    # A raw key issued to A is A-only: the record pins the organization itself.
    directory = env["directory"]
    assert isinstance(directory, ApiKeyDirectory)
    identity = await directory.lookup(issued.secret)
    assert identity is not None and identity.organization_id == ORG_A


async def test_composite_fk_rejects_cross_tenant_references(env: dict[str, object]) -> None:
    app_a = await _make_application(env, org=ORG_A, user=USER_A, name="victim-app")
    _app_issued, issued = await _issue_key(env, org=ORG_A, user=USER_A)
    key_a = issued.view.id

    # A key row cannot claim B's tenant with A's application (composite FK
    # AND the RLS WITH CHECK both stand in the way; either denial is a pass).
    with pytest.raises((IntegrityError, DBAPIError)) as excinfo:
        async with _app_conn(str(env["app_url"]), ORG_A) as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO api_keys (id, organization_id, application_id, "
                    "created_by_user_id, name, key_prefix, secret_hash, scopes, created_at, "
                    "updated_at) VALUES (:id, :org_b, :app_a, :user_a, 'mismatch', :prefix, "
                    ":hash, 'gateway:invoke', now(), now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "org_b": ORG_B,
                    "app_a": app_a.id,
                    "user_a": USER_A,
                    "prefix": f"{API_KEY_PREFIX}deadbeefdead",
                    "hash": token_digest(f"{API_KEY_PREFIX}{uuid.uuid4().hex}{uuid.uuid4().hex}"),
                },
            )
    assert _is_permission_denied(excinfo.value) or _is_fk_violation(excinfo.value), str(
        excinfo.value
    )

    # Nor can a key in B's context point at A's application or A's predecessor.
    with pytest.raises((IntegrityError, DBAPIError)) as excinfo:
        async with _app_conn(str(env["app_url"]), ORG_B) as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO api_keys (id, organization_id, application_id, "
                    "created_by_user_id, name, key_prefix, secret_hash, scopes, created_at, "
                    "updated_at, superseded_by_id) VALUES (:id, :org_b, :app_a, :user_b, "
                    "'steal', :prefix, :hash, 'gateway:invoke', now(), now(), :key_a)"
                ),
                {
                    "id": uuid.uuid4(),
                    "org_b": ORG_B,
                    "app_a": app_a.id,
                    "user_b": USER_B,
                    "prefix": f"{API_KEY_PREFIX}cafebabecafe",
                    "hash": token_digest(f"{API_KEY_PREFIX}{uuid.uuid4().hex}{uuid.uuid4().hex}"),
                    "key_a": key_a,
                },
            )
    assert _is_fk_violation(excinfo.value) or _is_permission_denied(excinfo.value), str(
        excinfo.value
    )


# ---------------------------------------------------------------------------
# Constraint-level guards (belt and braces under the Python validation)
# ---------------------------------------------------------------------------


async def test_database_checks_reject_malformed_key_rows(env: dict[str, object]) -> None:
    app = await _make_application(env, org=ORG_A, user=USER_A, name="checks-app")
    base_columns = (
        "id, organization_id, application_id, created_by_user_id, name, key_prefix, "
        "secret_hash, scopes, created_at, updated_at"
    )
    cases = {
        "bad prefix": {
            "prefix": "nope_1234",
            "hash": "a" * 64,
            "scopes": "gateway:invoke",
        },
        "short hash": {
            "prefix": f"{API_KEY_PREFIX}abcdefabcdef",
            "hash": "abc",
            "scopes": "gateway:invoke",
        },
        "empty scopes": {
            "prefix": f"{API_KEY_PREFIX}abcdefabcdef",
            "hash": "a" * 64,
            "scopes": "",
        },
    }
    for label, case in cases.items():
        with pytest.raises(DBAPIError, match="(?i)constraint|check") as excinfo:
            async with _app_conn(str(env["app_url"]), ORG_A) as conn:
                await conn.execute(
                    sa.text(
                        f"INSERT INTO api_keys ({base_columns}) VALUES "
                        "(:id, :org, :app, :user, 'row', :prefix, :hash, :scopes, now(), now())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "org": ORG_A,
                        "app": app.id,
                        "user": USER_A,
                        "prefix": case["prefix"],
                        "hash": case["hash"],
                        "scopes": case["scopes"],
                    },
                )
        assert excinfo.type is not None, label


async def test_digest_collisions_are_rejected(env: dict[str, object]) -> None:
    app_a = await _make_application(env, org=ORG_A, user=USER_A, name="first-app")
    digest = token_digest(f"{API_KEY_PREFIX}{'a' * 32}")
    insert = sa.text(
        "INSERT INTO api_keys (id, organization_id, application_id, created_by_user_id, "
        "name, key_prefix, secret_hash, scopes, created_at, updated_at) VALUES "
        "(:id, :org, :app, :user, 'dup', :prefix, :hash, 'gateway:invoke', now(), now())"
    )
    async with _app_conn(str(env["app_url"]), ORG_A) as conn:
        await conn.execute(
            insert,
            {
                "id": uuid.uuid4(),
                "org": ORG_A,
                "app": app_a.id,
                "user": USER_A,
                "prefix": f"{API_KEY_PREFIX}aaaaaaaaaaaa",
                "hash": digest,
            },
        )
    with pytest.raises(DBAPIError) as excinfo:
        async with _app_conn(str(env["app_url"]), ORG_A) as conn:
            await conn.execute(
                insert,
                {
                    "id": uuid.uuid4(),
                    "org": ORG_A,
                    "app": app_a.id,
                    "user": USER_A,
                    "prefix": f"{API_KEY_PREFIX}aaaaaaaaaaaa",
                    "hash": digest,
                },
            )
    orig = getattr(excinfo.value, "orig", excinfo.value)
    assert getattr(orig, "sqlstate", None) == "23505" or "duplicate key" in str(excinfo.value)


async def test_keyset_pagination_walks_and_rejects_garbage(env: dict[str, object]) -> None:
    store = env["store"]
    assert isinstance(store, PostgresApplicationStore)
    names = [f"page-app-{i}" for i in range(3)]
    for name in names:
        await _make_application(env, org=ORG_A, user=USER_A, name=name)

    first = await store.list_applications(organization_id=ORG_A, cursor=None, limit=2)
    assert len(first.items) == 2 and first.next_cursor is not None
    second = await store.list_applications(organization_id=ORG_A, cursor=first.next_cursor, limit=2)
    assert len(second.items) == 1 and second.next_cursor is None
    seen = {item.name for item in [*first.items, *second.items]}
    assert seen >= set(names[-2:]) or len(seen) == 3  # newest-first walk covers all

    with pytest.raises(AppError) as excinfo:
        await store.list_applications(organization_id=ORG_A, cursor="!!!not-base64!!!", limit=2)
    assert excinfo.value.code == "invalid_cursor"
