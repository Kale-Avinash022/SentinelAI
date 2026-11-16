"""tenants and rbac: Week 2 multi-tenant domain schema, grants and demo seed

Revision ID: 0002_tenants_and_rbac
Revises: 0001_baseline
Create Date: 2026-11-16

Day 009 applies the reviewed Day 008 models as a *constrained* schema and
pins the least-privilege boundary for the runtime ``sentinel_app`` role.

Design decisions (all deliberate, reviewed line by line — see step-009 §4):

* UUID primary keys, ``TIMESTAMPTZ`` timestamps and ``NUMERIC`` money columns
  come straight from the ORM models; cross-tenant composite foreign keys and
  CHECK constraints are rendered intact.
* The application role gets **DML only** and, within DML, only what its write
  paths need. ``SELECT``/``INSERT``/``UPDATE`` on tenant tables; the global
  provider/model catalog is read-only reference data (``SELECT`` only). No
  ``DELETE``, no ``TRUNCATE``, no ``REFERENCES``/``CREATE``/``ALTER`` — those
  stay with the migration owner ``sentinel_migrator`` (the schema owner set in
  ``infra/docker/postgres/init-roles.sh``). ``sentinel_app`` therefore cannot
  weaken or rewrite its own controls. Grants are declared explicitly here (not
  only via first-boot default privileges) so the boundary is version-controlled
  and reproducible in every environment, including freshly created databases.
* A synthetic demo tenant is seeded so Day 012 login and Day 051 two-party
  Category-B approval work out of the box: one owner plus two admins, with
  ``allow_self_approval`` left at its default-deny ``false`` (ADR-0004
  obligation). All identifiers are fixed UUIDs so the seed is deterministic.

Expand/contract discipline: this is a pure *expand* (new tables only) with a
full *contract* ``downgrade``; later breaking changes ship as separate
add-then-remove revisions so no deploy ever drops a column still in use.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_tenants_and_rbac"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Runtime (DML) role vs schema-owner (DDL) role, per init-roles.sh.
APP_ROLE = "sentinel_app"

# Global, read-only reference data: the application may only SELECT.
READ_ONLY_CATALOG_TABLES = ("provider_catalog_entries", "model_catalog_entries")
# Tenant-owned operational tables: the application may SELECT/INSERT/UPDATE.
TENANT_TABLES = (
    "organizations",
    "users",
    "role_assignments",
    "applications",
    "provider_configs",
    "provider_credentials",
)

# Deterministic synthetic demo identifiers (ADR-0004 §Consequences seed).
_DEMO_ORG_ID = "a1000000-0000-4000-8000-000000000001"
_OWNER_USER_ID = "a2000000-0000-4000-8000-000000000001"
_ADMIN1_USER_ID = "a2000000-0000-4000-8000-000000000002"
_ADMIN2_USER_ID = "a2000000-0000-4000-8000-000000000003"
_OWNER_ROLE_ID = "a3000000-0000-4000-8000-000000000001"
_ADMIN1_ROLE_ID = "a3000000-0000-4000-8000-000000000002"
_ADMIN2_ROLE_ID = "a3000000-0000-4000-8000-000000000003"


def _grant_stmt(privileges: str, tables: tuple[str, ...], role: str) -> str:
    cols = ", ".join(tables)
    return f"GRANT {privileges} ON TABLE {cols} TO {role}"


def upgrade() -> None:
    # --- Day 008 tenant domain, rendered from reviewed ORM metadata ---------
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("allow_self_approval", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        sa.UniqueConstraint("slug", name=op.f("uq_organizations_slug")),
    )
    op.create_table(
        "provider_catalog_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("supports_streaming", sa.Boolean(), nullable=False),
        sa.Column("supports_chat", sa.Boolean(), nullable=False),
        sa.Column("is_deterministic_mock", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_catalog_entries")),
        sa.UniqueConstraint("provider", name=op.f("uq_provider_catalog_entries_provider")),
    )
    op.create_table(
        "model_catalog_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("context_window_tokens", sa.Integer(), nullable=False),
        sa.Column("input_price_per_1k_tokens", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("output_price_per_1k_tokens", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "context_window_tokens > 0",
            name=op.f("ck_model_catalog_entries_positive_context_window"),
        ),
        sa.CheckConstraint(
            "input_price_per_1k_tokens >= 0",
            name=op.f("ck_model_catalog_entries_non_negative_input_price"),
        ),
        sa.CheckConstraint(
            "output_price_per_1k_tokens >= 0",
            name=op.f("ck_model_catalog_entries_non_negative_output_price"),
        ),
        sa.ForeignKeyConstraint(
            ["provider"],
            ["provider_catalog_entries.provider"],
            name=op.f("fk_model_catalog_entries_provider_provider_catalog_entries"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_catalog_entries")),
        sa.UniqueConstraint(
            "provider", "model_name", name=op.f("uq_model_catalog_entries_provider_model")
        ),
    )
    op.create_table(
        "provider_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("secret_ref", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "secret_ref LIKE 'secret://%' AND secret_ref NOT LIKE '%{%'",
            name=op.f("ck_provider_credentials_secret_ref_is_locator_not_payload"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'rotating')",
            name=op.f("ck_provider_credentials_valid_credential_status"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_provider_credentials_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["provider"],
            ["provider_catalog_entries.provider"],
            name=op.f("fk_provider_credentials_provider_provider_catalog_entries"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_credentials")),
        sa.UniqueConstraint(
            "organization_id",
            "provider",
            name=op.f("uq_provider_credentials_org_provider"),
        ),
    )
    op.create_index(
        op.f("ix_provider_credentials_organization_id"),
        "provider_credentials",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_users_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.UniqueConstraint("id", "organization_id", name=op.f("uq_users_id_org")),
    )
    op.create_index(op.f("ix_users_organization_id"), "users", ["organization_id"], unique=False)
    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name=op.f("ck_applications_valid_application_status"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_applications_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name=op.f("fk_applications_owner_user_org_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_applications")),
        sa.UniqueConstraint("organization_id", "name", name=op.f("uq_applications_org_name")),
    )
    op.create_index(
        op.f("ix_applications_organization_id"),
        "applications",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "role_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('admin', 'operator', 'owner', 'viewer')",
            name=op.f("ck_role_assignments_valid_role"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name=op.f("fk_role_assignments_user_org_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_assignments")),
        sa.UniqueConstraint("user_id", name=op.f("uq_role_assignments_user")),
    )
    op.create_index(
        op.f("ix_role_assignments_organization_id"),
        "role_assignments",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "provider_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("monthly_budget_usd", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "monthly_budget_usd IS NULL OR monthly_budget_usd >= 0",
            name=op.f("ck_provider_configs_non_negative_budget"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_provider_configs_application_id_applications"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_provider_configs_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["provider"],
            ["provider_catalog_entries.provider"],
            name=op.f("fk_provider_configs_provider_provider_catalog_entries"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_configs")),
        sa.UniqueConstraint(
            "organization_id",
            "provider",
            "model_name",
            name=op.f("uq_provider_configs_org_provider_model"),
        ),
    )
    op.create_index(
        op.f("ix_provider_configs_application_id"),
        "provider_configs",
        ["application_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_provider_configs_organization_id"),
        "provider_configs",
        ["organization_id"],
        unique=False,
    )

    # --- Least-privilege grants: DML only, DDL stays with sentinel_migrator --
    # Reset first: first-boot default privileges would otherwise have handed
    # sentinel_app DELETE on every new table. We then grant only what is needed.
    all_tables = ", ".join(READ_ONLY_CATALOG_TABLES + TENANT_TABLES)
    op.execute(f"REVOKE ALL ON TABLE {all_tables} FROM {APP_ROLE}")
    op.execute(_grant_stmt("SELECT", READ_ONLY_CATALOG_TABLES, APP_ROLE))
    op.execute(_grant_stmt("SELECT, INSERT, UPDATE", TENANT_TABLES, APP_ROLE))

    # --- Synthetic demo seed (ADR-0004): one owner + two admins, no self-approval
    op.execute(
        "INSERT INTO organizations (id, name, slug, is_active, allow_self_approval, "
        "created_at, updated_at) VALUES "
        f"('{_DEMO_ORG_ID}', 'Sentinel Demo Org', 'sentinel-demo', true, false, "
        "now(), now())"
    )
    op.execute(
        "INSERT INTO users (id, organization_id, email, display_name, password_hash, "
        "is_active, created_at, updated_at) VALUES "
        f"('{_OWNER_USER_ID}', '{_DEMO_ORG_ID}', 'owner@sentinel.demo', 'Demo Owner', "
        "NULL, true, now(), now()), "
        f"('{_ADMIN1_USER_ID}', '{_DEMO_ORG_ID}', 'admin1@sentinel.demo', 'Demo Admin One', "
        "NULL, true, now(), now()), "
        f"('{_ADMIN2_USER_ID}', '{_DEMO_ORG_ID}', 'admin2@sentinel.demo', 'Demo Admin Two', "
        "NULL, true, now(), now())"
    )
    op.execute(
        "INSERT INTO role_assignments (id, user_id, organization_id, role, created_at) VALUES "
        f"('{_OWNER_ROLE_ID}', '{_OWNER_USER_ID}', '{_DEMO_ORG_ID}', 'owner', now()), "
        f"('{_ADMIN1_ROLE_ID}', '{_ADMIN1_USER_ID}', '{_DEMO_ORG_ID}', 'admin', now()), "
        f"('{_ADMIN2_ROLE_ID}', '{_ADMIN2_USER_ID}', '{_DEMO_ORG_ID}', 'admin', now())"
    )


def downgrade() -> None:
    # Drop the demo seed implicitly with the tables; explicit revoke keeps the
    # role boundary tidy if the schema is later recreated in-place.
    all_tables = ", ".join(READ_ONLY_CATALOG_TABLES + TENANT_TABLES)
    op.execute(f"REVOKE ALL ON TABLE {all_tables} FROM {APP_ROLE}")
    op.drop_index(op.f("ix_provider_configs_organization_id"), table_name="provider_configs")
    op.drop_index(op.f("ix_provider_configs_application_id"), table_name="provider_configs")
    op.drop_table("provider_configs")
    op.drop_index(op.f("ix_role_assignments_organization_id"), table_name="role_assignments")
    op.drop_table("role_assignments")
    op.drop_index(op.f("ix_applications_organization_id"), table_name="applications")
    op.drop_table("applications")
    op.drop_index(op.f("ix_users_organization_id"), table_name="users")
    op.drop_table("users")
    op.drop_index(
        op.f("ix_provider_credentials_organization_id"), table_name="provider_credentials"
    )
    op.drop_table("provider_credentials")
    op.drop_table("model_catalog_entries")
    op.drop_table("provider_catalog_entries")
    op.drop_table("organizations")
