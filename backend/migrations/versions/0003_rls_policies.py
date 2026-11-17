"""rls policies: Day 010 forced row-level security on tenant-owned tables

Revision ID: 0003_rls_policies
Revises: 0002_tenants_and_rbac
Create Date: 2026-11-17

Day 010 adds the *database-enforced* organization boundary that survives
connection pooling. The Python query layer already filters by tenant, but a
forgotten ``WHERE`` or a tampered identifier must still be refused by the
database itself (steps/README.md design rule 4: "Tenant isolation is
foundational").

Design decisions (reviewed line by line — see step-010 §4):

* RLS is **enabled and forced** on every tenant-owned table. ``FORCE`` matters
  for the table owner: without it an owner silently bypasses the policies.
* Each policy is **PERMISSIVE**, applies to **all roles** (no ``TO`` clause) and
  compares the tenant column to
  ``NULLIF(current_setting('app.organization_id', true), '')::uuid``.
  ``missing_ok=true`` returns ``NULL`` when the GUC was never set, and PostgreSQL
  leaves an empty ``''`` on a reused pooled connection after a transaction-local
  value reverts at commit; ``NULLIF`` collapses both to ``NULL``, so
  ``organization_id = NULL::uuid`` is never true: reads return **zero** rows and
  writes fail the ``WITH CHECK`` — missing or stale context denies rather than
  broadens.
* The policy is *both* ``USING`` (read/modify visibility) and ``WITH CHECK``
  (new/updated rows must belong to the current tenant), so a cross-tenant write
  is rejected at the row boundary on top of the Day 009 composite-FK guards.
* The API role ``sentinel_app`` is a *non-owner* with ``NOBYPASSRLS`` (Day 004),
  so it is always subject to these policies and cannot change them (no DDL grant,
  Day 009). The separate admin/migration role ``sentinel_migrator`` is the table
  owner; because ``FORCE`` would otherwise lock the owner out, it relies on the
  ``BYPASSRLS`` attribute granted by the superuser bootstrap
  (``infra/docker/postgres/init-roles.sh``). ``BYPASSRLS`` is a superuser-only
  attribute and is therefore **not** granted here — a migration cannot elevate
  its own role — keeping the privileged admin path explicit and audited and the
  API role permanently non-bypassing.

Expand/contract discipline: ``downgrade`` drops the policies and disables RLS,
restoring the Day 009 schema exactly.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_rls_policies"
down_revision: str | None = "0002_tenants_and_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Transaction-local GUC set by the application inside the same transaction as
# every query (see app.infrastructure.database.tenant_session_scope). Never a
# session-wide value that could leak through a pooled connection.
TENANT_GUC = "app.organization_id"

# Tenant-owned tables and the column that holds their organization boundary.
# ``organizations`` keys on its own id; every other tenant table carries a
# non-null ``organization_id`` FK (Day 008/009).
TENANT_TABLES: tuple[tuple[str, str], ...] = (
    ("organizations", "id"),
    ("users", "organization_id"),
    ("role_assignments", "organization_id"),
    ("applications", "organization_id"),
    ("provider_configs", "organization_id"),
    ("provider_credentials", "organization_id"),
)

# Global read-only reference data (Day 009) is intentionally excluded: it is not
# tenant-owned and must stay visible to every organization.
POLICY_SUFFIX = "tenant_isolation"


def _policy_name(table: str) -> str:
    return f"{table}_{POLICY_SUFFIX}"


def _tenant_predicate(column: str) -> str:
    # NULLIF collapses both a never-set GUC (NULL) and the empty string that
    # PostgreSQL leaves behind on a pooled connection after SET LOCAL reverts at
    # commit, so missing/stale context always yields NULL and denies access rather
    # than broadening it (or erroring the cast on '').
    return f"{column} = NULLIF(current_setting('{TENANT_GUC}', true), '')::uuid"


def upgrade() -> None:
    for table, column in TENANT_TABLES:
        # Enable, then force: force is what makes the table owner obey the
        # policies too, so no privileged-in-name-but-not-in-truth role slips past.
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"CREATE POLICY {_policy_name(table)} ON {table} "
            f"AS PERMISSIVE FOR ALL "
            f"USING ({_tenant_predicate(column)}) "
            f"WITH CHECK ({_tenant_predicate(column)})"
        )


def downgrade() -> None:
    # Reverse order; disable RLS entirely to restore the Day 009 schema.
    for table, _column in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {_policy_name(table)} ON {table}")
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
