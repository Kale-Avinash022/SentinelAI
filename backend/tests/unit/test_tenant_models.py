"""Day 008 unit tests: tenant domain models, constraints and relationships.

Runs against in-memory SQLite so the gate stays hermetic (no Docker). SQLite
enforces the same CHECK expressions, composite foreign keys and unique
constraints that Day 009 will render through Alembic for PostgreSQL; the
runtime-DDL test proves the metadata actually creates every object.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import closing

import pytest
import sqlalchemy as sa
from app.auth.roles import Role
from app.models import Base, metadata
from app.models.application import Application
from app.models.provider_catalog import (
    CredentialStatus,
    ModelCatalogEntry,
    ProviderCatalogEntry,
    ProviderConfig,
    ProviderCredential,
)
from app.models.tenant import Organization
from app.models.user import RoleAssignment, User
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

TENANT_TABLES = (
    "users",
    "role_assignments",
    "applications",
    "provider_configs",
    "provider_credentials",
)
GLOBAL_TABLES = ("provider_catalog_entries", "model_catalog_entries")


@pytest.fixture
def engine() -> Iterator[sa.Engine]:
    eng = sa.create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with closing(eng.connect()) as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys = ON")
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: sa.Engine) -> Iterator[Session]:
    # JOINED inheritance-free plain objects; expire_on_commit=False keeps
    # attributes readable after flush/commit boundaries inside the test.
    with Session(engine, expire_on_commit=False) as sess:
        yield sess


def _make_org(session: Session, slug: str) -> Organization:
    org = Organization(name=f"Org {slug}", slug=slug)
    session.add(org)
    session.flush()
    return org


def _make_user(session: Session, org: Organization, email: str, role: Role) -> User:
    user = User(organization_id=org.id, email=email, display_name=email.split("@")[0])
    session.add(user)
    session.flush()  # populate the client-side PK default before referencing it
    session.add(RoleAssignment(user_id=user.id, organization_id=org.id, role=role.value))
    session.flush()
    return user


# ---------------------------------------------------------------------------
# Acceptance: every tenant-owned model has an organization boundary
# ---------------------------------------------------------------------------


def test_every_tenant_table_has_required_org_fk_to_organizations() -> None:
    # role_assignments is the deliberate exception: a column may hold only one
    # ForeignKey in SQL, and its organization_id is already inside the
    # composite (user_id, organization_id) -> users FK. Tenant validity is
    # enforced transitively: users.organization_id -> organizations.id, and no
    # users row can pair an id with a foreign organization.
    for name in TENANT_TABLES:
        table = metadata.tables[name]
        org_col = table.c["organization_id"]
        assert org_col.nullable is False, name
        assert org_col.foreign_keys, f"{name}.organization_id has no foreign key"
        if name == "role_assignments":
            ddl = str(sa.schema.CreateTable(table))
            assert "FOREIGN KEY(user_id, organization_id) REFERENCES users" in ddl, name
        else:
            ddl = str(sa.schema.CreateTable(table))
            assert "FOREIGN KEY(organization_id) REFERENCES organizations (id)" in ddl, name


def test_global_catalog_tables_have_no_org_column() -> None:
    for name in GLOBAL_TABLES:
        assert "organization_id" not in metadata.tables[name].c, name


def test_org_relationship_backfills_organization_id_on_flush(session: Session) -> None:
    org = _make_org(session, "backfill")
    app = Application(
        name="chat-proxy", owner_user_id=_make_user(session, org, "o@b.dev", Role.OWNER).id
    )
    org.applications.append(app)
    session.flush()
    assert app.organization_id == org.id


# ---------------------------------------------------------------------------
# Acceptance: constraints reject invalid roles and ownership combinations
# ---------------------------------------------------------------------------


def test_role_check_constraint_accepts_all_four_roles(session: Session) -> None:
    org = _make_org(session, "roles")
    for i, role in enumerate(Role):
        user = _make_user(session, org, f"u{i}@roles.dev", role)
        assert user.role is role


def test_role_check_constraint_rejects_unknown_role(session: Session) -> None:
    org = _make_org(session, "badrole")
    user = _make_user(session, org, "ok@badrole.dev", Role.VIEWER)
    session.add(RoleAssignment(user_id=user.id, organization_id=org.id, role="superuser"))
    with pytest.raises(IntegrityError, match="ck_role_assignments_valid_role"):
        session.flush()


def test_role_assignment_unique_per_user(session: Session) -> None:
    org = _make_org(session, "duprole")
    user = _make_user(session, org, "a@duprole.dev", Role.ADMIN)
    session.add(RoleAssignment(user_id=user.id, organization_id=org.id, role=Role.OWNER.value))
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
        session.flush()


def test_cross_tenant_role_assignment_rejected_by_composite_fk(session: Session) -> None:
    org_a = _make_org(session, "alpha")
    org_b = _make_org(session, "beta")
    user_a = _make_user(session, org_a, "a@alpha.dev", Role.VIEWER)
    session.add(RoleAssignment(user_id=user_a.id, organization_id=org_b.id, role=Role.ADMIN.value))
    with pytest.raises(IntegrityError):
        session.flush()


def test_cross_tenant_application_owner_rejected_by_composite_fk(session: Session) -> None:
    org_a = _make_org(session, "ownalpha")
    org_b = _make_org(session, "ownbeta")
    user_a = _make_user(session, org_a, "a@ownalpha.dev", Role.OWNER)
    session.add(
        Application(
            name="stolen",
            organization_id=org_b.id,  # claims tenant B ...
            owner_user_id=user_a.id,  # ... but the owner lives in tenant A
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_application_status_check_rejects_invalid_value(session: Session) -> None:
    org = _make_org(session, "status")
    owner = _make_user(session, org, "o@status.dev", Role.OWNER)
    session.add(
        Application(name="sick", organization_id=org.id, owner_user_id=owner.id, status="paused")
    )
    with pytest.raises(IntegrityError, match="ck_applications_valid_application_status"):
        session.flush()


def test_application_name_unique_within_org_across_orgs(session: Session) -> None:
    org_a = _make_org(session, "nalpha")
    org_b = _make_org(session, "nbeta")
    user_a = _make_user(session, org_a, "a@nalpha.dev", Role.OWNER)
    user_b = _make_user(session, org_b, "b@nbeta.dev", Role.OWNER)
    session.add(Application(name="dup", organization_id=org_a.id, owner_user_id=user_a.id))
    session.flush()
    session.add(Application(name="dup", organization_id=org_b.id, owner_user_id=user_b.id))
    session.flush()  # different org: allowed
    session.add(Application(name="dup", organization_id=org_b.id, owner_user_id=user_b.id))
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
        session.flush()


# ---------------------------------------------------------------------------
# Acceptance: provider secrets are references, not JSON credentials
# ---------------------------------------------------------------------------


def _catalog_provider(session: Session, provider: str = "openai") -> ProviderCatalogEntry:
    entry = ProviderCatalogEntry(
        provider=provider,
        display_name=provider.title(),
        supports_streaming=True,
        is_deterministic_mock=provider == "fake",
    )
    session.add(entry)
    session.add(
        ModelCatalogEntry(
            provider=provider,
            model_name=f"{provider}-mini",
            context_window_tokens=128_000,
            input_price_per_1k_tokens=0.15,
            output_price_per_1k_tokens=0.60,
        )
    )
    session.flush()
    return entry


def test_credential_secret_ref_accepts_locator_and_rejects_payloads(session: Session) -> None:
    org = _make_org(session, "secrets")
    _catalog_provider(session, "openai")
    good = ProviderCredential(
        organization_id=org.id,
        provider="openai",
        secret_ref="secret://env/OPENAI_API_KEY",
        status=CredentialStatus.ACTIVE.value,
    )
    session.add(good)
    session.flush()
    # Distinct providers per attempt so the org+provider UNIQUE never masks
    # the CHECK violation we are actually probing.
    for i, bad_ref in enumerate(
        (
            "sk-live-abcdef1234567890",
            '{"api_key": "super-secret"}',
            'secret://json/{"key": "value"}',
        )
    ):
        provider = f"leak{i}"
        _catalog_provider(session, provider)
        with session.begin_nested():
            session.add(
                ProviderCredential(organization_id=org.id, provider=provider, secret_ref=bad_ref)
            )
            with pytest.raises(IntegrityError, match="secret_ref_is_locator_not_payload"):
                session.flush()


def test_credential_status_check_and_provider_fk(session: Session) -> None:
    org = _make_org(session, "credstate")
    _catalog_provider(session, "fake")
    with session.begin_nested():
        session.add(
            ProviderCredential(
                organization_id=org.id,
                provider="fake",
                secret_ref="secret://local",
                status="burned",
            )
        )
        with pytest.raises(IntegrityError, match="ck_provider_credentials_valid_credential_status"):
            session.flush()
    with session.begin_nested():
        session.add(
            ProviderCredential(
                organization_id=org.id,
                provider="notacatalogprovider",
                secret_ref="secret://x",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_provider_config_must_reference_catalog_provider(session: Session) -> None:
    org = _make_org(session, "cfgfk")
    _catalog_provider(session, "fake")
    with session.begin_nested():
        session.add(ProviderConfig(organization_id=org.id, provider="ghost", model_name="g-1"))
        with pytest.raises(IntegrityError):
            session.flush()
    config = ProviderConfig(organization_id=org.id, provider="fake", model_name="fake-mini")
    session.add(config)
    session.flush()
    assert config.catalog_entry is not None
    assert config.catalog_entry.is_deterministic_mock is True


def test_provider_config_budget_check(session: Session) -> None:
    org = _make_org(session, "cfgbudget")
    _catalog_provider(session, "fake")
    session.add(
        ProviderConfig(
            organization_id=org.id, provider="fake", model_name="m", monthly_budget_usd=-5
        )
    )
    with pytest.raises(IntegrityError, match="ck_provider_configs_non_negative_budget"):
        session.flush()


def test_model_catalog_price_checks(session: Session) -> None:
    _catalog_provider(session, "priced")
    session.add(
        ModelCatalogEntry(
            provider="priced",
            model_name="free-but-broken",
            context_window_tokens=0,
            input_price_per_1k_tokens=-1,
            output_price_per_1k_tokens=0,
        )
    )
    with pytest.raises(IntegrityError, match="ck_model_catalog_entries_positive_context_window"):
        session.flush()


# ---------------------------------------------------------------------------
# Relationship navigation used by Day 012+ code paths
# ---------------------------------------------------------------------------


def test_user_role_and_applications_navigation(session: Session) -> None:
    org = _make_org(session, "nav")
    owner = _make_user(session, org, "boss@nav.dev", Role.OWNER)
    app = Application(name="navigator", organization_id=org.id, owner_user_id=owner.id)
    session.add(app)
    session.flush()
    assert owner.role is Role.OWNER
    assert app.owner is owner
    assert app in owner.applications
    assert app in org.applications


def test_defaults_populate_tz_aware_timestamps_and_ids(session: Session) -> None:
    org = _make_org(session, "defaults")
    assert org.id is not None
    assert org.created_at.tzinfo is not None
    assert org.allow_self_approval is False  # default-deny policy at the row level
    assert org.is_active is True


def test_organization_self_approval_flag_persists(session: Session) -> None:
    org = _make_org(session, "flag")
    org.allow_self_approval = True
    session.flush()
    loaded = session.get(Organization, org.id)
    assert loaded is not None and loaded.allow_self_approval is True


def test_unrelated_uuid_ids_are_distinct_and_stable(session: Session) -> None:
    first = uuid.uuid4()
    org = Organization(name="Direct id", slug="direct", id=first)
    session.add(org)
    session.flush()
    assert org.id == first
