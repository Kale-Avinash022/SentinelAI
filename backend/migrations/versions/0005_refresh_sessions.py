"""refresh sessions + login identity lookup: Day 012 authentication

Revision ID: 0005_refresh_sessions
Revises: 0004_nested_tenant_fk
Create Date: 2026-11-18

Day 012 adds revocable human authentication:

* ``refresh_sessions`` — a tenant-owned, RLS-protected table storing only the
  **SHA-256 digest** of each rotating refresh token (never the token), its
  rotation ``family_id`` and expiry/revocation state. It is bound to its user by
  the composite ``(user_id, organization_id) -> users`` foreign key, matching the
  Day 009/011 cross-tenant guards, so a session can never name another tenant's
  user.
* The table gets the exact same privilege shape as the other tenant tables:
  ``sentinel_app`` may ``SELECT``/``INSERT``/``UPDATE`` but **not** ``DELETE`` —
  tokens are retired by marking ``revoked_at`` (which also preserves the row so
  replay of a rotated token is detectable), never removed.
* ``app_lookup_login_identity(text)`` — the single, explicit way to resolve a
  login by its globally-unique email *before* any tenant context exists. RLS
  hides ``users`` rows from the context-less API role by design, so this is a
  ``SECURITY DEFINER`` function owned by the BYPASSRLS migration role: it runs
  with the owner's rights, is pinned to a safe ``search_path``, is not callable
  by ``PUBLIC``, and returns only the four login-critical columns for the one
  matching email. No password ever crosses the boundary (Argon2 verification is
  done in the application); only the stored digest is returned to the app's own
  database connection.

Expand/contract discipline: pure ``upgrade`` (new table + function) with a full
``downgrade``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_refresh_sessions"
down_revision: str | None = "0004_nested_tenant_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "sentinel_app"
TABLE = "refresh_sessions"
POLICY = "refresh_sessions_tenant_isolation"
TENANT_GUC = "app.organization_id"
LOOKUP_FN = "app_lookup_login_identity"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(token_hash) = 64",
            name=op.f("ck_refresh_sessions_valid_token_hash_length"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_refresh_sessions_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name=op.f("fk_refresh_sessions_user_org_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_sessions_token_hash")),
    )
    op.create_index(
        op.f("ix_refresh_sessions_organization_id"), TABLE, ["organization_id"], unique=False
    )
    op.create_index(op.f("ix_refresh_sessions_user_id"), TABLE, ["user_id"], unique=False)
    op.create_index(op.f("ix_refresh_sessions_family_id"), TABLE, ["family_id"], unique=False)
    op.create_index(op.f("ix_refresh_sessions_expires_at"), TABLE, ["expires_at"], unique=False)

    # Least-privilege DML: first-boot default privileges would have granted
    # DELETE too; narrow to SELECT/INSERT/UPDATE exactly like the other tenant
    # tables (revocation is an UPDATE, never a DELETE).
    op.execute(f"REVOKE ALL ON TABLE {TABLE} FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON TABLE {TABLE} TO {APP_ROLE}")

    # Same forced, permissive, all-role tenant-isolation policy as revision 0003.
    op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
    predicate = f"organization_id = NULLIF(current_setting('{TENANT_GUC}', true), '')::uuid"
    op.execute(
        f"CREATE POLICY {POLICY} ON {TABLE} "
        f"AS PERMISSIVE FOR ALL "
        f"USING ({predicate}) "
        f"WITH CHECK ({predicate})"
    )

    # Context-free login resolution: SECURITY DEFINER owned by the BYPASSRLS
    # migration role, pinned search_path, not PUBLIC-executable.
    op.execute(
        f"""
        CREATE FUNCTION {LOOKUP_FN}(p_email text)
        RETURNS TABLE (user_id uuid, organization_id uuid, password_hash text, is_active boolean)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT u.id, u.organization_id, u.password_hash, u.is_active
            FROM users u
            WHERE u.email = p_email
        $$;
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {LOOKUP_FN}(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {LOOKUP_FN}(text) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON FUNCTION {LOOKUP_FN}(text) FROM {APP_ROLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {LOOKUP_FN}(text)")
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
    op.execute(f'ALTER TABLE "{TABLE}" DISABLE ROW LEVEL SECURITY')
    op.execute(f"REVOKE ALL ON TABLE {TABLE} FROM {APP_ROLE}")
    op.drop_index(op.f("ix_refresh_sessions_expires_at"), table_name=TABLE)
    op.drop_index(op.f("ix_refresh_sessions_family_id"), table_name=TABLE)
    op.drop_index(op.f("ix_refresh_sessions_user_id"), table_name=TABLE)
    op.drop_index(op.f("ix_refresh_sessions_organization_id"), table_name=TABLE)
    op.drop_table(TABLE)
