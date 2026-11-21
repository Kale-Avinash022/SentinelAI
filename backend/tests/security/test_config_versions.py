"""Day 014 security tests: immutable versions, CAS activation and no leakage.

The unit suite pins the *rules*; this one pins the four acceptance checks as
facts the database enforces, because a rule that only lives in Python is a
convention someone can bypass with one stray ``UPDATE``. Mirroring the Day 010
audit harness, against live PostgreSQL and as the real unprivileged
``sentinel_app`` role:

* **History cannot be edited in place.** ``UPDATE`` and ``DELETE`` on
  ``config_versions`` are denied, and on ``config_activations`` only the
  ``superseded_at`` column is writable — closing a record, never rewriting it.
* **Activation is compare-and-swap.** A stale expectation is refused with
  nothing written, two concurrent activations leave exactly one live row, and
  the partial unique index catches even a hand-crafted second "live" record.
* **Diffs redact and secrets stay external.** Bodies only ever leave as
  fingerprints, and the credential-shaped CHECKs are probed with raw SQL so a
  bug that skips Python still cannot store a secret.
* **Last-known-good must be earned.** The label is refused without
  qualification by both the service and the database.

Runs against the disposable ``sentinel_ai_test`` database, migrated to head;
the development database is untouched.
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
from app.auth.keys import API_KEY_PREFIX
from app.configs.vault import FernetVault
from app.core.errors import AppError
from app.infrastructure.database import (
    application_database_url,
    create_session_factory,
    migration_database_url,
    set_tenant_context,
)
from app.services.applications import NotFoundError
from app.services.config_versions import (
    ActivationConflictError,
    ActivationView,
    BodyNotAvailableError,
    BodyUndecryptableError,
    ConfigVersionView,
    IncompatibleConfigurationError,
    InvalidConfigPayloadError,
    PostgresConfigVersionStore,
    QualificationRequiredError,
)
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio(loop_scope="module")

TEST_DB = "sentinel_ai_test"
REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic.ini"

ORG_A = uuid.UUID("f8000000-0000-4000-8000-00000000000a")
ORG_B = uuid.UUID("f8000000-0000-4000-8000-00000000000b")
USER_A = uuid.UUID("f9000000-0000-4000-8000-00000000000a")
USER_A2 = uuid.UUID("f9000000-0000-4000-8000-00000000000c")
USER_B = uuid.UUID("f9000000-0000-4000-8000-00000000000b")

MASTER_SECRET = "day-014-security-suite-master-secret-0123"  # nosec B105 - test-only key
ROTATED_SECRET = "a-completely-different-master-secret-01234"  # nosec B105 - test-only key
VAULT = FernetVault(MASTER_SECRET)

PROMPT_BODY = "You are a triage assistant for {{customer_name}}. Ticket: {{ticket_summary}}"
OTHER_BODY = "You are a terse triage assistant."
SECRET_REF = "secret://vault/triage-prompt-v1"

#: ``openai``/``anthropic`` exist in the catalog (only openai streams);
#: ``mistral`` is deliberately absent so the compatibility gate has something
#: to refuse. Deterministic mocks only — no provider is ever contacted.
CATALOG_PROVIDERS: tuple[tuple[str, bool], ...] = (("openai", True), ("anthropic", False))

#: The composite ``(user, organization)`` foreign keys make a cross-tenant actor
#: structurally impossible, so every write names that tenant's own user.
ORGANIZATION_ACTORS: dict[uuid.UUID, uuid.UUID] = {ORG_A: USER_A, ORG_B: USER_B}


def route_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "primary_provider": "openai",
        "allowed_providers": ["openai", "anthropic"],
        "model_name": "gpt-4o-mini",
        "capabilities": {"streaming": True, "tools": False, "vision": False},
        "privacy_class": "internal",
        "quality_floor": 0.8,
        "timeout_ms": 5000,
        "max_input_tokens": 8000,
        "monthly_budget_usd": 500.0,
        "evaluator_version": "eval-v2",
    }
    base.update(overrides)
    return base


def prompt_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "provider": "openai",
        "model_name": "gpt-4o-mini",
        "variables": ["customer_name", "ticket_summary"],
        "output_format": "text",
        "max_output_tokens": 800,
        "quality_floor": 0.7,
        "evaluator_version": "eval-v2",
    }
    base.update(overrides)
    return base


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _is_permission_denied(exc: BaseException) -> bool:
    """42501 grant/RLS failures surface in a few shapes; accept all of them."""
    orig = getattr(exc, "orig", exc)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate == "42501":
        return True
    text_ = str(exc).lower()
    return "permission denied" in text_ or "row-level security" in text_


def _violates(exc: BaseException, constraint: str) -> bool:
    """True when the failure is the named CHECK (23514) or unique (23505) rule."""
    return constraint in str(exc)


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def config_db() -> AsyncIterator[dict[str, str]]:
    """Create sentinel_ai_test, migrate to head, seed tenants, users and catalog."""
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

    await asyncio.to_thread(command.upgrade, _alembic_config(_render(test_migrator_url)), "head")

    engine = create_async_engine(_render(test_migrator_url), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for org in (ORG_A, ORG_B):
                await conn.execute(
                    sa.text(
                        "INSERT INTO organizations (id, name, slug, is_active, "
                        "allow_self_approval, created_at, updated_at) VALUES "
                        "(:org, :name, :slug, true, false, now(), now())"
                    ),
                    {"org": org, "name": f"Org {str(org)[-1]}", "slug": f"ver-{str(org)[-1]}"},
                )
            # ORG_A has two operators so the concurrency test races two
            # distinct actors that both legitimately belong to the tenant.
            for org, user, email in (
                (ORG_A, USER_A, "alice@versions.test"),
                (ORG_A, USER_A2, "alex@versions.test"),
                (ORG_B, USER_B, "bob@versions.test"),
            ):
                await conn.execute(
                    sa.text(
                        "INSERT INTO users (id, organization_id, email, display_name, "
                        "password_hash, is_active, created_at, updated_at) VALUES "
                        "(:user, :org, :email, 'User', "
                        "'$argon2id$placeholder-not-used-for-login', true, now(), now())"
                    ),
                    {"user": user, "org": org, "email": email},
                )
            for provider, streams in CATALOG_PROVIDERS:
                await conn.execute(
                    sa.text(
                        "INSERT INTO provider_catalog_entries (id, provider, display_name, "
                        "supports_streaming, supports_chat, is_deterministic_mock, created_at) "
                        "VALUES (gen_random_uuid(), :provider, :provider, :streams, true, "
                        ":streams, now())"
                    ),
                    {"provider": provider, "streams": streams},
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
async def store(config_db: dict[str, str]) -> AsyncIterator[PostgresConfigVersionStore]:
    """Production store as ``sentinel_app``; the migrator wipes the history after."""
    engine = create_async_engine(config_db["app_url"], poolclass=NullPool)
    try:
        yield PostgresConfigVersionStore(create_session_factory(engine), VAULT)
    finally:
        await engine.dispose()
        migrator = create_async_engine(
            config_db["migrator_url"], isolation_level="AUTOCOMMIT", poolclass=NullPool
        )
        async with migrator.begin() as conn:
            await conn.execute(sa.text("DELETE FROM config_activations"))
            await conn.execute(sa.text("DELETE FROM config_versions"))
            await conn.execute(sa.text("DELETE FROM audit_logs"))
        await migrator.dispose()


# ---------------------------------------------------------------------------
# Helpers that go through the production service
# ---------------------------------------------------------------------------


async def _route_version(
    store: PostgresConfigVersionStore,
    name: str,
    *,
    organization_id: uuid.UUID = ORG_A,
    actor: uuid.UUID | None = None,
    **overrides: Any,
) -> ConfigVersionView:
    return await store.create_version(
        organization_id=organization_id,
        actor_user_id=actor or ORGANIZATION_ACTORS[organization_id],
        kind="provider_route",
        name=name,
        payload=route_payload(**overrides),
        request_id=f"req-route-{name}",
    )


async def _prompt_version(
    store: PostgresConfigVersionStore,
    name: str,
    *,
    body: str | None = PROMPT_BODY,
    secret_ref: str | None = None,
    organization_id: uuid.UUID = ORG_A,
    payload: dict[str, Any] | None = None,
) -> ConfigVersionView:
    return await store.create_version(
        organization_id=organization_id,
        actor_user_id=ORGANIZATION_ACTORS[organization_id],
        kind="prompt",
        name=name,
        payload=payload if payload is not None else prompt_payload(),
        body=body,
        secret_ref=secret_ref,
        request_id=f"req-prompt-{name}",
    )


async def _activate(
    store: PostgresConfigVersionStore,
    name: str,
    version_id: uuid.UUID,
    expected: int | None,
    *,
    kind: str = "provider_route",
    organization_id: uuid.UUID = ORG_A,
    actor: uuid.UUID | None = None,
    qualification: dict[str, Any] | None = None,
    is_last_known_good: bool = False,
) -> ActivationView:
    return await store.activate(
        organization_id=organization_id,
        actor_user_id=actor or ORGANIZATION_ACTORS[organization_id],
        kind=kind,
        name=name,
        version_id=version_id,
        expected_previous_number=expected,
        qualification=qualification,
        is_last_known_good=is_last_known_good,
        request_id=f"req-activate-{name}-{version_id}",
    )


async def _history(
    store: PostgresConfigVersionStore, name: str, *, kind: str = "provider_route"
) -> list[ActivationView]:
    page = await store.list_activations(
        organization_id=ORG_A, kind=kind, name=name, cursor=None, limit=10
    )
    return list(page.items)


# ---------------------------------------------------------------------------
# Acceptance check 1: historical versions cannot be edited in place
# ---------------------------------------------------------------------------


async def test_config_versions_cannot_be_updated_or_deleted(
    store: PostgresConfigVersionStore, config_db: dict[str, str]
) -> None:
    """The grant is ``SELECT`` + ``INSERT`` and nothing else, so there is no
    writable path to a stored version — not even for its own tenant."""
    version = await _route_version(store, "immutable-route")

    for statement in (
        "UPDATE config_versions SET version_number = 99 WHERE id = :id",
        # The interesting one: model_name lives inside JSON, so rewriting
        # history "legitimately" means touching payload — equally denied.
        "UPDATE config_versions SET payload = '{}' WHERE id = :id",
        "UPDATE config_versions SET encrypted_payload = NULL WHERE id = :id",
        "DELETE FROM config_versions WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as excinfo:
            async with _app_conn(config_db["app_url"], ORG_A) as conn:
                await conn.execute(sa.text(statement), {"id": version.id})
        assert _is_permission_denied(excinfo.value), f"{statement}: {excinfo.value}"

    # The row survives every attempted rewrite, exactly as it was written.
    reread = await store.get_version(organization_id=ORG_A, version_id=version.id)
    assert reread.version_number == 1
    assert reread.content_hash == version.content_hash
    assert reread.payload == version.payload


async def test_activation_history_may_only_be_closed_not_rewritten(
    store: PostgresConfigVersionStore, config_db: dict[str, str]
) -> None:
    """``UPDATE`` is granted on exactly one column, ``superseded_at``: a record
    can be retired, never re-attributed — and never deleted."""
    v1 = await _route_version(store, "cas-column")
    activation = await _activate(store, "cas-column", v1.id, None)

    for statement in (
        # Re-pointing the live record at a different version.
        "UPDATE config_activations SET version_number = 99 WHERE id = :id",
        "UPDATE config_activations SET is_last_known_good = true WHERE id = :id",
        "UPDATE config_activations SET expected_previous_number = 42 WHERE id = :id",
        "DELETE FROM config_activations WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as excinfo:
            async with _app_conn(config_db["app_url"], ORG_A) as conn:
                await conn.execute(sa.text(statement), {"id": activation.id})
        assert _is_permission_denied(excinfo.value), f"{statement}: {excinfo.value}"

    # The one permitted write: closing the record.
    async with _app_conn(config_db["app_url"], ORG_A) as conn:
        await conn.execute(
            sa.text("UPDATE config_activations SET superseded_at = now() WHERE id = :id"),
            {"id": activation.id},
        )
    with pytest.raises(NotFoundError):
        await store.get_active(organization_id=ORG_A, kind="provider_route", name="cas-column")
    # And the closed record is still there — history is retained, not moved.
    assert [item.version_id for item in await _history(store, "cas-column")] == [v1.id]


# ---------------------------------------------------------------------------
# Acceptance check 2: activation uses an expected-version check + audit history
# ---------------------------------------------------------------------------


async def test_activation_records_audit_history(store: PostgresConfigVersionStore) -> None:
    v1 = await _route_version(store, "cas-audit")
    first = await _activate(store, "cas-audit", v1.id, None)
    v2 = await _route_version(store, "cas-audit")
    second = await _activate(store, "cas-audit", v2.id, expected=1)

    assert first.is_active is True
    assert second.version_number == 2
    assert second.expected_previous_number == 1
    assert second.supersedes_id == first.id

    history = await _history(store, "cas-audit")
    assert [item.version_number for item in history] == [2, 1]
    assert history[0].is_active is True
    assert history[1].is_active is False
    assert history[1].superseded_at is not None


async def test_stale_expected_version_conflicts_and_writes_nothing(
    store: PostgresConfigVersionStore,
) -> None:
    v1 = await _route_version(store, "cas-stale")
    await _activate(store, "cas-stale", v1.id, None)
    v2 = await _route_version(store, "cas-stale")

    with pytest.raises(ActivationConflictError, match="Expected previous version"):
        await _activate(store, "cas-stale", v2.id, expected=99)

    # Refusal is total: v1 is still live and no orphan activation exists.
    live = await store.get_active(organization_id=ORG_A, kind="provider_route", name="cas-stale")
    assert live.version_id == v1.id
    assert [item.version_id for item in await _history(store, "cas-stale")] == [v1.id]


async def test_activating_from_nothing_with_an_expectation_conflicts(
    store: PostgresConfigVersionStore,
) -> None:
    """Expecting version 3 while nothing is live means the writer is looking at
    a slot that is not the one it thinks it is."""
    v1 = await _route_version(store, "cas-empty")
    with pytest.raises(ActivationConflictError, match="Nothing is active"):
        await _activate(store, "cas-empty", v1.id, expected=3)


async def test_concurrent_activations_leave_exactly_one_live_record(
    store: PostgresConfigVersionStore, config_db: dict[str, str]
) -> None:
    """The step's real requirement: two writers who both think they are first.

    Whichever code path the interleaving takes — the row lock plus the
    zero-row ``UPDATE``, or the partial unique index — exactly one activation
    wins and the slot keeps a single source of truth.
    """
    v1 = await _route_version(store, "cas-race")
    await _activate(store, "cas-race", v1.id, None)
    v2 = await _route_version(store, "cas-race")
    v3 = await _route_version(store, "cas-race")

    # Two distinct operators of the *same* tenant, so the composite actor
    # foreign key cannot be the thing that settles the race.
    results = await asyncio.gather(
        _activate(store, "cas-race", v2.id, expected=1, actor=USER_A),
        _activate(store, "cas-race", v3.id, expected=1, actor=USER_A2),
        return_exceptions=True,
    )
    won = [item for item in results if isinstance(item, ActivationView)]
    failed = [item for item in results if isinstance(item, BaseException)]
    assert len(won) == 1, f"expected exactly one winner, got {results!r}"
    assert len(failed) == 1
    assert isinstance(failed[0], AppError), repr(failed[0])

    live = await store.get_active(organization_id=ORG_A, kind="provider_route", name="cas-race")
    assert live.is_active is True
    assert live.version_id == won[0].version_id

    async with _app_conn(config_db["app_url"], ORG_A) as conn:
        open_records = (
            await conn.execute(
                sa.text(
                    "SELECT count(*) FROM config_activations "
                    "WHERE kind = 'provider_route' AND name = 'cas-race' "
                    "AND superseded_at IS NULL"
                )
            )
        ).scalar_one()
        # The loser's transaction left nothing behind, trail included.
        activations_audited = (
            await conn.execute(
                sa.text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE action = 'config_activation.created' AND organization_id = :org"
                ),
                {"org": ORG_A},
            )
        ).scalar_one()
    assert open_records == 1
    assert activations_audited == 2  # the setup activation plus the single winner


async def test_partial_unique_index_refuses_a_second_live_record_by_sql(
    store: PostgresConfigVersionStore, config_db: dict[str, str]
) -> None:
    """Even a writer that skips the service cannot open a second live record."""
    v1 = await _route_version(store, "cas-index")
    activation = await _activate(store, "cas-index", v1.id, None)

    with pytest.raises(DBAPIError) as excinfo:
        async with _app_conn(config_db["app_url"], ORG_A) as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO config_activations (id, organization_id, kind, name, version_id, "
                    "version_number, expected_previous_number, supersedes_id, "
                    "activated_by_user_id, activated_at, superseded_at, qualification, "
                    "is_last_known_good) VALUES (:id, :org, 'provider_route', 'cas-index', :vid, "
                    "1, NULL, NULL, :actor, now(), NULL, NULL, false)"
                ),
                {"id": uuid.uuid4(), "org": ORG_A, "vid": activation.version_id, "actor": USER_A},
            )
    assert "uq_config_activations_live_slot" in str(excinfo.value), str(excinfo.value)


# ---------------------------------------------------------------------------
# Acceptance check 3: diffs redact, credentials stay external references
# ---------------------------------------------------------------------------


async def test_diff_of_prompt_bodies_returns_fingerprints_only(
    store: PostgresConfigVersionStore,
) -> None:
    """A diff answers "did the template change?" and can never answer "what
    was it?" — the ciphertext column is not even selected into this path."""
    v1 = await _prompt_version(store, "diff-body", body=PROMPT_BODY)
    v2 = await _prompt_version(store, "diff-body", body=OTHER_BODY)

    diff = await store.diff_versions(
        organization_id=ORG_A, before_version_id=v1.id, after_version_id=v2.id
    )

    assert diff.identical is False
    assert [entry.path for entry in diff.entries] == ["body"]
    entry = diff.entries[0]
    assert entry.change == "changed"
    assert str(entry.before).startswith("sha256:")
    assert str(entry.after).startswith("sha256:")
    assert entry.before != entry.after
    assert PROMPT_BODY not in repr(diff.entries)
    assert OTHER_BODY not in repr(diff.entries)
    assert v1.body_storage == "encrypted"


async def test_identical_versions_diff_as_identical(store: PostgresConfigVersionStore) -> None:
    """Re-sealing the same text produces different ciphertext, yet the version
    identity is the content hash over the plaintext, so the diff stays honest."""
    v1 = await _prompt_version(store, "diff-same", body=PROMPT_BODY)
    v2 = await _prompt_version(store, "diff-same", body=PROMPT_BODY)
    diff = await store.diff_versions(
        organization_id=ORG_A, before_version_id=v1.id, after_version_id=v2.id
    )
    assert diff.identical is True
    assert diff.entries == []
    assert diff.body_change_unverified is False
    assert v1.content_hash == v2.content_hash


async def test_diff_reports_metadata_changes_and_flags_unattributed_body(
    store: PostgresConfigVersionStore,
) -> None:
    v1 = await _prompt_version(store, "diff-meta", body=PROMPT_BODY)
    v2 = await _prompt_version(
        store, "diff-meta", body=OTHER_BODY, payload=prompt_payload(quality_floor=0.95)
    )

    diff = await store.diff_versions(
        organization_id=ORG_A, before_version_id=v1.id, after_version_id=v2.id
    )
    paths = {entry.path: entry for entry in diff.entries}
    assert paths["payload.quality_floor"].change == "changed"
    assert paths["payload.quality_floor"].before == 0.7
    assert paths["payload.quality_floor"].after == 0.95
    # The hash moved and metadata already explains part of it, so the template
    # *may* also have changed — reported as uncertainty, never as a guess.
    assert "body" not in paths
    assert diff.body_change_unverified is True


async def test_diff_refuses_to_compare_across_ladders(
    store: PostgresConfigVersionStore,
) -> None:
    a = await _route_version(store, "diff-ladder-a")
    b = await _route_version(store, "diff-ladder-b")
    with pytest.raises(InvalidConfigPayloadError, match="same"):
        await store.diff_versions(
            organization_id=ORG_A, before_version_id=a.id, after_version_id=b.id
        )


async def test_stored_payload_column_rejects_credential_shaped_values_by_sql(
    store: PostgresConfigVersionStore, config_db: dict[str, str]
) -> None:
    """The metadata column is plain text, so the *database* polices what may
    appear in it: a Python bug cannot store a credential or a sealed body."""
    version = await _route_version(store, "leak-probe")
    template = await store.get_version(organization_id=ORG_A, version_id=version.id)
    clean = str(template.payload)

    for forbidden in (
        '"api_key": "x"',
        '"apikey": "x"',
        '"password": "x"',
        '"ref": "secret://vault/key"',
        f'"raw": "{API_KEY_PREFIX}abc123"',
        f'"sealed": "{VAULT.seal(PROMPT_BODY)}"',
    ):
        payload = clean.rstrip("}") + "," + forbidden + "}"
        with pytest.raises(DBAPIError) as excinfo:
            async with _app_conn(config_db["app_url"], ORG_A) as conn:
                await conn.execute(
                    sa.text(
                        "INSERT INTO config_versions (id, organization_id, kind, name, "
                        "version_number, content_hash, payload, secret_ref, encrypted_payload, "
                        "created_by_user_id, created_at) VALUES (:id, :org, 'provider_route', "
                        "'leak-probe', 99, :hash, :payload, NULL, NULL, :actor, now())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "org": ORG_A,
                        "hash": "a" * 64,
                        "payload": payload,
                        "actor": USER_A,
                    },
                )
        assert "ck_config_versions_payload_carries_no_credential" in str(excinfo.value), (
            f"{forbidden}: {excinfo.value}"
        )


async def test_stored_rows_reject_inline_secret_references_and_naked_prompts(
    store: PostgresConfigVersionStore, config_db: dict[str, str]
) -> None:
    """Two more database facts: a reference must be a ``secret://`` locator, and
    a prompt body must be sealed or referenced — never absent, never inline."""
    cases: tuple[tuple[str, dict[str, Any], str], ...] = (
        # (label, sensitive columns, expected constraint)
        (
            "inline payload posing as a reference",
            {"secret_ref": '{"key": "sk_live_x"}', "encrypted_payload": None},
            "ck_config_versions_secret_ref_is_locator_not_payload",
        ),
        (
            "prompt with neither body nor reference",
            {"secret_ref": None, "encrypted_payload": None},
            "ck_config_versions_prompt_body_is_sealed_or_referenced",
        ),
        (
            "provider route smuggling a sealed body",
            {"secret_ref": None, "encrypted_payload": VAULT.seal(PROMPT_BODY)},
            "ck_config_versions_route_version_has_no_sensitive_body",
        ),
        (
            "blank sealed body",
            {"secret_ref": None, "encrypted_payload": ""},
            "ck_config_versions_encrypted_payload_not_blank",
        ),
    )
    probe = await _route_version(store, "shape-probe")
    base = (await store.get_version(organization_id=ORG_A, version_id=probe.id)).payload
    payload_text = str(base)

    for index, (label, sensitive, constraint) in enumerate(cases):
        with pytest.raises(DBAPIError) as excinfo:
            async with _app_conn(config_db["app_url"], ORG_A) as conn:
                await conn.execute(
                    sa.text(
                        "INSERT INTO config_versions (id, organization_id, kind, name, "
                        "version_number, content_hash, payload, secret_ref, encrypted_payload, "
                        "created_by_user_id, created_at) VALUES (:id, :org, :kind, :name, :num, "
                        ":hash, :payload, :secret_ref, :encrypted_payload, :actor, now())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "org": ORG_A,
                        # A prompt kind for the reference/body cases, a route for
                        # the smuggling case, so each probe isolates one CHECK.
                        "kind": "provider_route" if index == 2 else "prompt",
                        "name": f"shape-probe-{index}",
                        "num": index + 1,
                        "hash": "b" * 64,
                        # Prompt payloads differ from route payloads, but this
                        # column only has to satisfy the credential CHECK.
                        "payload": payload_text,
                        **sensitive,
                        "actor": USER_A,
                    },
                )
        assert constraint in str(excinfo.value), f"{label}: {excinfo.value}"


async def test_reference_only_version_keeps_the_locator_out_of_every_response(
    store: PostgresConfigVersionStore,
) -> None:
    """The locator itself is not a secret to the tenant, but it is never
    echoed: the read model exposes a fingerprint and nothing else."""
    version = await _prompt_version(store, "ref-ok", body=None, secret_ref=SECRET_REF)
    assert version.body_storage == "reference"
    assert version.secret_ref_fingerprint is not None
    assert SECRET_REF not in repr(version)

    with pytest.raises(InvalidConfigPayloadError, match="secret:// locator"):
        await _prompt_version(store, "ref-ok", body=None, secret_ref="sk_live_abc123")
    with pytest.raises(InvalidConfigPayloadError, match="secret:// locator"):
        await _prompt_version(store, "ref-ok", body=None, secret_ref='{"key": "x"}')


async def test_provider_route_versions_never_carry_a_body(
    store: PostgresConfigVersionStore,
) -> None:
    with pytest.raises(InvalidConfigPayloadError, match="cannot carry a prompt body"):
        await store.create_version(
            organization_id=ORG_A,
            actor_user_id=USER_A,
            kind="provider_route",
            name="route-body",
            payload=route_payload(),
            body="smuggled template text",
            request_id="req-route-body",
        )


# ---------------------------------------------------------------------------
# Acceptance check 4: last-known-good requires explicit qualification evidence
# ---------------------------------------------------------------------------


async def test_last_known_good_is_refused_without_qualification(
    store: PostgresConfigVersionStore,
) -> None:
    v1 = await _route_version(store, "lkg-route")
    with pytest.raises(QualificationRequiredError, match="qualification evidence"):
        await _activate(store, "lkg-route", v1.id, None, is_last_known_good=True)
    # Rejected before the write: the slot is still untouched.
    with pytest.raises(NotFoundError):
        await store.get_active(organization_id=ORG_A, kind="provider_route", name="lkg-route")


async def test_database_repeats_the_qualification_rule(
    store: PostgresConfigVersionStore, config_db: dict[str, str]
) -> None:
    """A writer that skips the service still cannot claim the label for free."""
    v1 = await _route_version(store, "lkg-sql")
    with pytest.raises(DBAPIError) as excinfo:
        async with _app_conn(config_db["app_url"], ORG_A) as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO config_activations (id, organization_id, kind, name, version_id, "
                    "version_number, expected_previous_number, supersedes_id, "
                    "activated_by_user_id, activated_at, superseded_at, qualification, "
                    "is_last_known_good) VALUES (:id, :org, 'provider_route', 'lkg-sql', :vid, "
                    "1, NULL, NULL, :actor, now(), NULL, NULL, true)"
                ),
                {"id": uuid.uuid4(), "org": ORG_A, "vid": v1.id, "actor": USER_A},
            )
    assert "ck_config_activations_lkg_requires_evidence" in str(excinfo.value), str(excinfo.value)


async def test_last_known_good_is_derived_from_qualifying_activations(
    store: PostgresConfigVersionStore,
) -> None:
    v1 = await _route_version(store, "lkg-derive")
    await _activate(
        store,
        "lkg-derive",
        v1.id,
        None,
        qualification={"evaluation_run": "run-1", "score": 0.91, "approver": "release-board"},
        is_last_known_good=True,
    )
    v2 = await _route_version(store, "lkg-derive")
    await _activate(store, "lkg-derive", v2.id, expected=1)  # promoted, but unqualified

    good = await store.get_last_known_good(
        organization_id=ORG_A, kind="provider_route", name="lkg-derive"
    )
    live = await store.get_active(organization_id=ORG_A, kind="provider_route", name="lkg-derive")
    assert live.version_id == v2.id
    assert good.version_id == v1.id  # the earned label, not the newest row
    assert good.is_active is False
    assert good.qualification is not None
    assert good.qualification["evaluation_run"] == "run-1"


async def test_slot_without_any_qualifying_activation_has_no_last_known_good(
    store: PostgresConfigVersionStore,
) -> None:
    v1 = await _route_version(store, "lkg-none")
    await _activate(store, "lkg-none", v1.id, None)
    with pytest.raises(NotFoundError):
        await store.get_last_known_good(
            organization_id=ORG_A, kind="provider_route", name="lkg-none"
        )


async def test_qualification_may_not_smuggle_secret_material(
    store: PostgresConfigVersionStore,
) -> None:
    v1 = await _route_version(store, "lkg-smuggle")
    for qualification in (
        {"note": f"key is {API_KEY_PREFIX}abc123"},
        {"nested": [{"sealed": VAULT.seal(PROMPT_BODY)}]},
        {"fernet": [PROMPT_BODY, "gAAAAAbogus-token"]},
    ):
        with pytest.raises(InvalidConfigPayloadError, match="secret or sealed-payload"):
            await _activate(
                store,
                "lkg-smuggle",
                v1.id,
                None,
                qualification=qualification,
                is_last_known_good=True,
            )
    assert await _history(store, "lkg-smuggle") == []


# ---------------------------------------------------------------------------
# Compatibility gates and tenant isolation
# ---------------------------------------------------------------------------


async def test_activation_requires_providers_to_exist_in_the_catalog(
    store: PostgresConfigVersionStore,
) -> None:
    """A well-formed version that could not actually serve traffic is refused.

    Both rejections are at activation time, not creation time: a version may be
    drafted against a provider that is not onboarded yet, but it can never go
    live, so the catalog stays the authority on what is servable.
    """
    v1 = await _route_version(
        store, "compat-unknown", primary_provider="mistral", allowed_providers=["mistral"]
    )
    with pytest.raises(IncompatibleConfigurationError, match="not in the global catalog"):
        await _activate(store, "compat-unknown", v1.id, None)

    v2 = await _route_version(
        store, "compat-stream", primary_provider="anthropic", allowed_providers=["anthropic"]
    )
    with pytest.raises(IncompatibleConfigurationError, match="does not support streaming"):
        await _activate(store, "compat-stream", v2.id, None)
    assert await _history(store, "compat-unknown") == []


async def test_foreign_prompt_version_cannot_be_referenced(
    store: PostgresConfigVersionStore,
) -> None:
    """The reference lives inside JSON, so no foreign key can police it — the
    tenant-scoped read at activation time is what stops the cross-tenant route."""
    foreign = await _prompt_version(store, "triage", organization_id=ORG_B)
    route = await _route_version(store, "cross-tenant", prompt_version_id=str(foreign.id))

    with pytest.raises(IncompatibleConfigurationError, match="does not exist in this organization"):
        await _activate(store, "cross-tenant", route.id, None)

    # The same reference resolved inside the owning tenant is fine.
    own = await _prompt_version(store, "own-triage", organization_id=ORG_B)
    theirs = await _route_version(
        store, "same-tenant", prompt_version_id=str(own.id), organization_id=ORG_B
    )
    activated = await _activate(store, "same-tenant", theirs.id, None, organization_id=ORG_B)
    assert activated.is_active is True


async def test_versions_are_invisible_across_tenants(store: PostgresConfigVersionStore) -> None:
    mine = await _route_version(store, "tenant-private")
    with pytest.raises(NotFoundError):
        await store.get_version(organization_id=ORG_B, version_id=mine.id)
    page = await store.list_versions(
        organization_id=ORG_B, kind="provider_route", name="tenant-private", cursor=None, limit=10
    )
    assert page.items == []
    with pytest.raises(NotFoundError):
        await store.reveal_body(
            organization_id=ORG_B, actor_user_id=USER_B, version_id=mine.id, request_id="req-x"
        )
    with pytest.raises(NotFoundError):
        await _activate(store, "tenant-private", mine.id, None, organization_id=ORG_B)


async def test_tenant_context_is_required_to_see_any_history(
    store: PostgresConfigVersionStore, config_db: dict[str, str]
) -> None:
    await _route_version(store, "no-context")
    async with _app_conn(config_db["app_url"]) as conn:
        versions = (
            await conn.execute(sa.text("SELECT count(*) FROM config_versions"))
        ).scalar_one()
        activations = (
            await conn.execute(sa.text("SELECT count(*) FROM config_activations"))
        ).scalar_one()
    assert versions == 0
    assert activations == 0


# ---------------------------------------------------------------------------
# Sealed bodies: readable by the tenant, never present in the clear elsewhere
# ---------------------------------------------------------------------------


async def test_reveal_body_round_trips_and_is_audited_without_plaintext(
    store: PostgresConfigVersionStore, config_db: dict[str, str]
) -> None:
    """Reading a template is a privileged, *recorded* action: the step's
    "envelope encryption and access audit if stored"."""
    version = await _prompt_version(store, "reveal-body", body=PROMPT_BODY)

    first = await store.reveal_body(
        organization_id=ORG_A, actor_user_id=USER_A, version_id=version.id, request_id="req-r1"
    )
    second = await store.reveal_body(
        organization_id=ORG_A, actor_user_id=USER_B, version_id=version.id, request_id="req-r2"
    )
    assert first.body == PROMPT_BODY
    assert second.body == PROMPT_BODY
    assert first.content_hash == version.content_hash

    async with _app_conn(config_db["app_url"], ORG_A) as conn:
        reveals = (
            await conn.execute(
                sa.text(
                    "SELECT actor_id, detail FROM audit_logs "
                    "WHERE action = 'config_version.body_revealed' AND resource_id = :id "
                    "ORDER BY request_id"
                ),
                {"id": version.id},
            )
        ).all()
        stored = (
            await conn.execute(
                sa.text("SELECT encrypted_payload, payload FROM config_versions WHERE id = :id"),
                {"id": version.id},
            )
        ).one()
        unclean_trail = (
            await conn.execute(
                sa.text(
                    "SELECT count(*) FROM audit_logs WHERE detail LIKE '%sk_live_%' "
                    "OR detail LIKE '%gAAAAA%' OR detail LIKE '%fernet:%'"
                )
            )
        ).scalar_one()

    assert [str(row[0]) for row in reveals] == [str(USER_A), str(USER_B)]
    assert all(PROMPT_BODY not in str(row[1]) for row in reveals)
    assert stored.encrypted_payload.startswith("fernet:v1:")
    assert PROMPT_BODY not in stored.encrypted_payload
    assert PROMPT_BODY not in stored.payload
    assert unclean_trail == 0


async def test_reference_only_version_has_nothing_to_reveal(
    store: PostgresConfigVersionStore,
) -> None:
    version = await _prompt_version(store, "reveal-ref", body=None, secret_ref=SECRET_REF)
    assert version.body_storage == "reference"
    with pytest.raises(BodyNotAvailableError):
        await store.reveal_body(
            organization_id=ORG_A, actor_user_id=USER_A, version_id=version.id, request_id="req-n"
        )


async def test_wrong_key_fails_closed_without_leaking_vault_internals(
    store: PostgresConfigVersionStore, config_db: dict[str, str]
) -> None:
    """A body sealed under a rotated key is unrecoverable by design; the caller
    gets a retryable error, not a cryptography traceback with ciphertext in it."""
    version = await _prompt_version(store, "reveal-rotated", body=PROMPT_BODY)
    engine = create_async_engine(config_db["app_url"], poolclass=NullPool)
    try:
        other = PostgresConfigVersionStore(
            create_session_factory(engine), FernetVault(ROTATED_SECRET)
        )
        with pytest.raises(BodyUndecryptableError) as excinfo:
            await other.reveal_body(
                organization_id=ORG_A,
                actor_user_id=USER_A,
                version_id=version.id,
                request_id="req-k",
            )
    finally:
        await engine.dispose()
    assert PROMPT_BODY not in str(excinfo.value)
    assert "gAAAAA" not in str(excinfo.value)


async def test_audit_trail_refuses_ciphertext_at_the_database_level(
    store: PostgresConfigVersionStore, config_db: dict[str, str]
) -> None:
    """The widened CHECK is the last line of defence: even a direct INSERT with
    a sealed body in ``detail`` is rejected."""
    version = await _prompt_version(store, "audit-check", body=PROMPT_BODY)
    for forbidden in (f"{API_KEY_PREFIX}leak", VAULT.seal(PROMPT_BODY), "gAAAAA-forged-token"):
        with pytest.raises(DBAPIError) as excinfo:
            async with _app_conn(config_db["app_url"], ORG_A) as conn:
                await conn.execute(
                    sa.text(
                        "INSERT INTO audit_logs (id, organization_id, actor_type, actor_id, "
                        "action, resource_type, resource_id, detail, request_id, created_at) "
                        "VALUES (:id, :org, 'user', :actor, 'integration.probe', "
                        "'config_version', :resource_id, :detail, 'req-probe', now())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "org": ORG_A,
                        "actor": USER_A,
                        "resource_id": version.id,
                        "detail": f'{{"leak": "{forbidden}"}}',
                    },
                )
        assert "ck_audit_logs_detail_no_secret_material" in str(excinfo.value), str(excinfo.value)


async def test_version_numbers_are_assigned_by_the_database_not_by_clients(
    store: PostgresConfigVersionStore,
) -> None:
    """The ladder position comes from the server, so two writers racing for the
    same next number clash on the unique constraint instead of both surviving."""
    names = [f"ladder-{index}" for index in range(3)]
    for name in names:
        await _route_version(store, name)
    versions = [
        await store.list_versions(
            organization_id=ORG_A, kind="provider_route", name=name, cursor=None, limit=10
        )
        for name in names
    ]
    assert [len(page.items) for page in versions] == [1, 1, 1]
    assert {page.items[0].version_number for page in versions} == {1}

    for expected in (2, 3):
        made = await _route_version(store, names[0])
        assert made.version_number == expected
