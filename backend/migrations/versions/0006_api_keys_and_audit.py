"""scoped API keys + append-only audit: Day 013 credential lifecycle

Revision ID: 0006_api_keys_and_audit
Revises: 0005_refresh_sessions
Create Date: 2026-11-19

Day 013 adds machine credentials and the trail that accounts for them:

* ``api_keys`` — tenant-owned hashed keys. Only the SHA-256 ``secret_hash``
  (globally unique, so one raw key maps to at most one row) and a public
  ``key_prefix`` are stored; the raw ``sk_live_...`` value exists only in the
  create response. Application, creating user and rotation predecessor are
  bound by *composite* (pointer, organization_id) foreign keys, extending the
  Day 011 rule that a naked FK can resolve rows RLS is meant to hide.
  Grants: ``SELECT/INSERT/UPDATE`` and **no DELETE** — revocation is an UPDATE
  and superseded rows stay as evidence, mirroring ``refresh_sessions``.
* ``audit_logs`` — the append-only trail. Grants are exactly ``SELECT`` and
  ``INSERT``: the application role can *never* UPDATE or DELETE an audit row
  (retirement-by-expiry is a separate, future retention identity — Day 061 —
  never the API identity). A CHECK rejects any ``detail`` containing the
  ``sk_live_`` marker, so the database itself refuses a plaintext key in the
  trail. Rows carry a ``request_id`` for call-level accountability.
* Both tables get the exact Day 010 forced-RLS predicate shape.
* ``app_lookup_api_key(text)`` — the second reviewed ``SECURITY DEFINER``
  escape hatch (after Day 012's login lookup): a presented raw key must be
  resolved to its owning organization *before* any tenant context exists, so
  the lookup takes the key's SHA-256 digest and returns the owning row's
  identity columns. It never returns the digest or any secret, is pinned to a
  safe ``search_path``, is not callable by ``PUBLIC``, and is executable only
  by ``sentinel_app``.

Expand/contract discipline: pure additive ``upgrade`` with a full reverse
``downgrade``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_api_keys_and_audit"
down_revision: str | None = "0005_refresh_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "sentinel_app"
KEYS_TABLE = "api_keys"
KEYS_POLICY = "api_keys_tenant_isolation"
AUDIT_TABLE = "audit_logs"
AUDIT_POLICY = "audit_logs_tenant_isolation"
TENANT_GUC = "app.organization_id"
LOOKUP_FN = "app_lookup_api_key"


def _tenant_predicate() -> str:
    # Identical shape to revision 0003: NULLIF collapses the never-set NULL
    # and the post-revert empty string to NULL, which matches no organization.
    return f"organization_id = NULLIF(current_setting('{TENANT_GUC}', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        KEYS_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("key_prefix", sa.String(length=32), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.String(length=200), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "key_prefix LIKE 'sk_live_%'",
            name=op.f("ck_api_keys_valid_key_prefix"),
        ),
        sa.CheckConstraint(
            "length(secret_hash) = 64",
            name=op.f("ck_api_keys_valid_secret_hash_length"),
        ),
        sa.CheckConstraint(
            "scopes <> ''",
            name=op.f("ck_api_keys_non_empty_scopes"),
        ),
        sa.CheckConstraint(
            "superseded_by_id IS NULL OR superseded_by_id <> id",
            name=op.f("ck_api_keys_no_self_supersession"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id", "organization_id"],
            ["applications.id", "applications.organization_id"],
            name=op.f("fk_api_keys_application_org_applications"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name=op.f("fk_api_keys_created_by_user_org_users"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_api_keys_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id", "organization_id"],
            ["api_keys.id", "api_keys.organization_id"],
            name=op.f("fk_api_keys_superseded_by_org_api_keys"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("id", "organization_id", name=op.f("uq_api_keys_id_org")),
        sa.UniqueConstraint("secret_hash", name=op.f("uq_api_keys_secret_hash")),
    )
    op.create_index(
        op.f("ix_api_keys_organization_id"), KEYS_TABLE, ["organization_id"], unique=False
    )
    op.create_index(
        op.f("ix_api_keys_application_id"), KEYS_TABLE, ["application_id"], unique=False
    )
    op.create_index(op.f("ix_api_keys_expires_at"), KEYS_TABLE, ["expires_at"], unique=False)

    op.create_table(
        AUDIT_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("resource_type", sa.String(length=48), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actor_type IN ('api_key', 'system', 'user')",
            name=op.f("ck_audit_logs_valid_actor_type"),
        ),
        sa.CheckConstraint(
            "detail IS NULL OR detail NOT LIKE '%sk_live_%'",
            name=op.f("ck_audit_logs_detail_not_raw_key"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_audit_logs_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        op.f("ix_audit_logs_organization_id"), AUDIT_TABLE, ["organization_id"], unique=False
    )
    op.create_index(op.f("ix_audit_logs_resource_id"), AUDIT_TABLE, ["resource_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_created_at"), AUDIT_TABLE, ["created_at"], unique=False)

    # Least-privilege DML. api_keys: no DELETE (revocation is an UPDATE and
    # superseded rows remain as evidence). audit_logs: SELECT + INSERT ONLY —
    # the append-only guarantee is a grant fact, not a code convention.
    op.execute(f"REVOKE ALL ON TABLE {KEYS_TABLE} FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON TABLE {KEYS_TABLE} TO {APP_ROLE}")
    op.execute(f"REVOKE ALL ON TABLE {AUDIT_TABLE} FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE {AUDIT_TABLE} TO {APP_ROLE}")

    # Same forced, permissive, all-role tenant-isolation policy as 0003/0005.
    predicate = _tenant_predicate()
    for table, policy in ((KEYS_TABLE, KEYS_POLICY), (AUDIT_TABLE, AUDIT_POLICY)):
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"CREATE POLICY {policy} ON {table} "
            f"AS PERMISSIVE FOR ALL "
            f"USING ({predicate}) "
            f"WITH CHECK ({predicate})"
        )

    # Context-free key resolution, exactly one reviewed exception (mirrors
    # app_lookup_login_identity from revision 0005). Takes the *digest*, so
    # the raw secret never crosses into SQL text, logs or parameters.
    op.execute(
        f"""
        CREATE FUNCTION {LOOKUP_FN}(p_secret_hash text)
        RETURNS TABLE (
            key_id uuid,
            organization_id uuid,
            application_id uuid,
            scopes text,
            expires_at timestamptz,
            revoked_at timestamptz
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT k.id, k.organization_id, k.application_id,
                   k.scopes, k.expires_at, k.revoked_at
            FROM api_keys k
            WHERE k.secret_hash = p_secret_hash
        $$;
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {LOOKUP_FN}(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {LOOKUP_FN}(text) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON FUNCTION {LOOKUP_FN}(text) FROM {APP_ROLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {LOOKUP_FN}(text)")
    for table, policy in ((AUDIT_TABLE, AUDIT_POLICY), (KEYS_TABLE, KEYS_POLICY)):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
        op.execute(f"REVOKE ALL ON TABLE {table} FROM {APP_ROLE}")
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name=AUDIT_TABLE)
    op.drop_index(op.f("ix_audit_logs_resource_id"), table_name=AUDIT_TABLE)
    op.drop_index(op.f("ix_audit_logs_organization_id"), table_name=AUDIT_TABLE)
    op.drop_table(AUDIT_TABLE)
    op.drop_index(op.f("ix_api_keys_expires_at"), table_name=KEYS_TABLE)
    op.drop_index(op.f("ix_api_keys_application_id"), table_name=KEYS_TABLE)
    op.drop_index(op.f("ix_api_keys_organization_id"), table_name=KEYS_TABLE)
    op.drop_table(KEYS_TABLE)
