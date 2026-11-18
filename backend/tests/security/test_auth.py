"""Day 012 security tests: authentication persistence against real PostgreSQL.

These run the production :class:`app.auth.repository.PostgresAuthRepository` and
the revision ``0005`` database objects exactly as shipped, as the least-privilege
API role ``sentinel_app`` (``NOBYPASSRLS``). They prove the parts a fake store
cannot: the context-free ``SECURITY DEFINER`` login lookup, RLS isolation of
``refresh_sessions``, the no-DELETE grant, durable rotation, and family
revocation on reuse.

Requires the local Docker core stack (same as the Day 010/011 suites); a
disposable ``sentinel_ai_test`` is created, migrated and dropped around the
module so the development database is never touched.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.auth.repository import PostgresAuthRepository, RotateStatus
from app.auth.security import hash_password, token_digest, verify_password
from app.core.time import utcnow
from app.infrastructure.database import (
    application_database_url,
    create_session_factory,
    migration_database_url,
    set_tenant_context,
)
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio(loop_scope="module")

TEST_DB = "sentinel_ai_test"
REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic.ini"

PASSWORD = "correct-horse-durability"
ORG_A = uuid.UUID("e1000000-0000-4000-8000-00000000000a")
ORG_B = uuid.UUID("e1000000-0000-4000-8000-00000000000b")
USER_A = uuid.UUID("e2000000-0000-4000-8000-00000000000a")
USER_B = uuid.UUID("e2000000-0000-4000-8000-00000000000b")
ROLE_A = uuid.UUID("e4000000-0000-4000-8000-00000000000a")
ROLE_B = uuid.UUID("e4000000-0000-4000-8000-00000000000b")
FAMILY = uuid.UUID("e5000000-0000-4000-8000-00000000000f")


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    return config


async def _alembic(url: str, revision: str) -> None:
    await asyncio.to_thread(command.upgrade, _alembic_config(url), revision)


def _is_permission_denied(exc: BaseException) -> bool:
    orig = getattr(exc, "orig", exc)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate == "42501":
        return True
    text_ = str(exc).lower()
    return "permission denied" in text_ or "row-level security" in text_


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def auth_db() -> AsyncIterator[dict[str, str]]:
    """Create sentinel_ai_test, migrate to head, seed two users with hashes."""
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

    # Seed two orgs, each an owner with a real Argon2id digest, via the admin
    # (BYPASSRLS) path. Parameters keep the "$"-laden hash out of the SQL text.
    digest = hash_password(PASSWORD)
    engine = create_async_engine(_render(test_migrator_url), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for org, user, role, email in (
                (ORG_A, USER_A, ROLE_A, "alice@auth.test"),
                (ORG_B, USER_B, ROLE_B, "bob@auth.test"),
            ):
                await conn.execute(
                    sa.text(
                        "INSERT INTO organizations (id, name, slug, is_active, "
                        "allow_self_approval, created_at, updated_at) VALUES "
                        "(:org, :name, :slug, true, false, now(), now())"
                    ),
                    {"org": org, "name": f"Org {email}", "slug": f"auth-{str(org)[-1]}"},
                )
                await conn.execute(
                    sa.text(
                        "INSERT INTO users (id, organization_id, email, display_name, "
                        "password_hash, is_active, created_at, updated_at) VALUES "
                        "(:user, :org, :email, 'User', :digest, true, now(), now())"
                    ),
                    {"user": user, "org": org, "email": email, "digest": digest},
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
async def repo(auth_db: dict[str, str]) -> AsyncIterator[PostgresAuthRepository]:
    """A PostgresAuthRepository bound to the least-privilege API role.

    Teardown clears ``refresh_sessions`` through the privileged migrator role
    (the API role is deliberately granted no DELETE), so each test starts from
    an empty session table regardless of the rows a prior test rotated in.
    """
    engine = create_async_engine(auth_db["app_url"], poolclass=NullPool)
    try:
        yield PostgresAuthRepository(create_session_factory(engine))
    finally:
        await engine.dispose()
        migrator = create_async_engine(
            auth_db["migrator_url"], isolation_level="AUTOCOMMIT", poolclass=NullPool
        )
        try:
            async with migrator.begin() as conn:
                await conn.execute(sa.text("DELETE FROM refresh_sessions"))
        finally:
            await migrator.dispose()


def _future() -> Any:
    return utcnow() + timedelta(days=1)


# ---------------------------------------------------------------------------
# Acceptance: the SECURITY DEFINER login lookup resolves identity context-free
# ---------------------------------------------------------------------------
async def test_lookup_login_identity_is_context_free(repo: PostgresAuthRepository) -> None:
    identity = await repo.lookup_login_identity("alice@auth.test")
    assert identity is not None
    assert identity.user_id == USER_A
    assert identity.organization_id == ORG_A
    assert identity.is_active is True
    # The stored digest verifies the real password and never leaks it.
    assert verify_password(PASSWORD, identity.password_hash) is True
    assert PASSWORD not in (identity.password_hash or "")


async def test_lookup_login_identity_unknown_is_none(repo: PostgresAuthRepository) -> None:
    assert await repo.lookup_login_identity("nobody@auth.test") is None


# ---------------------------------------------------------------------------
# Acceptance: role is resolved from the database, under RLS, never from a claim
# ---------------------------------------------------------------------------
async def test_membership_role_is_authoritative_and_tenant_scoped(
    repo: PostgresAuthRepository,
) -> None:
    membership = await repo.get_membership(user_id=USER_A, organization_id=ORG_A)
    assert membership is not None
    assert membership.role == "owner"
    assert membership.is_active is True
    # Querying user A *as if* tenant B: RLS hides A's row -> no membership.
    assert await repo.get_membership(user_id=USER_A, organization_id=ORG_B) is None


# ---------------------------------------------------------------------------
# Acceptance: rotation is durable and reuse revokes the whole family
# ---------------------------------------------------------------------------
async def test_rotation_reuse_burns_the_family(repo: PostgresAuthRepository) -> None:
    h0, h1, h2 = (token_digest(t) for t in ("raw-0-abc", "raw-1-def", "raw-2-ghi"))
    await repo.create_session(
        user_id=USER_A, organization_id=ORG_A, family_id=FAMILY, token_hash=h0, expires_at=_future()
    )
    rotated = await repo.rotate_session(
        organization_id=ORG_A, presented_hash=h0, new_hash=h1, new_expires_at=_future()
    )
    assert rotated.status is RotateStatus.ROTATED
    assert rotated.family_id == FAMILY

    # Replaying the retired token is theft: REUSED, and the family is revoked.
    replay = await repo.rotate_session(
        organization_id=ORG_A, presented_hash=h0, new_hash=h2, new_expires_at=_future()
    )
    assert replay.status is RotateStatus.REUSED

    # The legitimate successor is now dead too (family-wide revocation).
    successor = await repo.rotate_session(
        organization_id=ORG_A, presented_hash=h1, new_hash=h2, new_expires_at=_future()
    )
    assert successor.status is RotateStatus.REUSED


async def test_unknown_token_is_not_reuse(repo: PostgresAuthRepository) -> None:
    missing = await repo.rotate_session(
        organization_id=ORG_A,
        presented_hash=token_digest("never-issued"),
        new_hash=token_digest("x"),
        new_expires_at=_future(),
    )
    assert missing.status is RotateStatus.UNKNOWN


async def test_rotate_is_invisible_across_tenants(repo: PostgresAuthRepository) -> None:
    h0 = token_digest("cross-tenant-raw")
    await repo.create_session(
        user_id=USER_A, organization_id=ORG_A, family_id=FAMILY, token_hash=h0, expires_at=_future()
    )
    # Same hash, but under tenant B's context RLS hides the A-owned row.
    foreign = await repo.rotate_session(
        organization_id=ORG_B,
        presented_hash=h0,
        new_hash=token_digest("y"),
        new_expires_at=_future(),
    )
    assert foreign.status is RotateStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Acceptance: rehash-on-login writes under the tenant context
# ---------------------------------------------------------------------------
async def test_update_password_hash_roundtrips(repo: PostgresAuthRepository) -> None:
    new_digest = hash_password("rotated-secret-password")
    await repo.update_password_hash(user_id=USER_A, organization_id=ORG_A, password_hash=new_digest)
    identity = await repo.lookup_login_identity("alice@auth.test")
    assert identity is not None
    assert verify_password("rotated-secret-password", identity.password_hash) is True
    assert verify_password(PASSWORD, identity.password_hash) is False


# ---------------------------------------------------------------------------
# Acceptance: refresh_sessions is least-privilege (no DELETE) and RLS-scoped
# ---------------------------------------------------------------------------
async def test_app_role_cannot_delete_refresh_sessions(auth_db: dict[str, str]) -> None:
    """Revocation is an UPDATE; the API role is never granted DELETE."""
    engine = create_async_engine(auth_db["app_url"], poolclass=NullPool)
    try:
        with pytest.raises(DBAPIError) as excinfo:
            async with engine.begin() as conn:
                await set_tenant_context(conn, ORG_A)
                await conn.execute(sa.text("DELETE FROM refresh_sessions WHERE TRUE"))
        assert _is_permission_denied(excinfo.value), str(excinfo.value)
    finally:
        await engine.dispose()


async def test_missing_context_exposes_no_sessions(
    repo: PostgresAuthRepository, auth_db: dict[str, str]
) -> None:
    await repo.create_session(
        user_id=USER_A,
        organization_id=ORG_A,
        family_id=FAMILY,
        token_hash=token_digest("no-context-raw"),
        expires_at=_future(),
    )
    engine = create_async_engine(auth_db["app_url"], poolclass=NullPool)
    try:
        async with engine.begin() as conn:  # no tenant context set
            count = (await conn.execute(sa.text("SELECT count(*) FROM refresh_sessions"))).scalar()
        assert count == 0, f"refresh_sessions leaked {count} rows without tenant context"
    finally:
        await engine.dispose()
