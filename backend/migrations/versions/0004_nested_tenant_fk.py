"""nested tenant FK isolation: Day 011 cross-tenant nested-reference guard

Revision ID: 0004_nested_tenant_fk
Revises: 0003_rls_policies
Create Date: 2026-11-17

Day 011's cross-tenant negative suite (``backend/tests/security/``) proved that
row-level security hides *foreign rows from reads*, but it also exposed a hole
RLS alone cannot close: ``provider_configs.application_id`` pointed at
``applications.id`` through a **single-column** foreign key. PostgreSQL does not
apply RLS to a table while checking a foreign key, so a row owned by
organization A that carried ``application_id`` = some application in organization
B was accepted: the RLS ``WITH CHECK`` only validated the config row's own
``organization_id`` (A), and the naked FK happily resolved B's otherwise-invisible
application. That is a nested IDOR — a cross-tenant reference smuggled through a
pointer column RLS never inspects.

The fix mirrors the pattern Day 009 already used for
``applications.owner_user_id`` and ``role_assignments.user_id``: bind the pointer
and the tenant together in one **composite** foreign key so the database, not
application convention, guarantees the referenced row shares the same
organization.

* ``applications`` gains ``UNIQUE (id, organization_id)`` — a valid composite FK
  target (``id`` is already the primary key, so this adds no new selectivity, it
  just lets another table reference the pair).
* ``provider_configs`` drops ``fk_provider_configs_application_id_applications``
  and re-adds ``(application_id, organization_id) -> applications(id,
  organization_id)``. A config whose ``organization_id`` is A can now only point
  at an application that *also* belongs to A; pointing at B's application
  violates the FK (23501/23503) regardless of what any code forgets to check.
  ``application_id`` stays nullable, and the default MATCH SIMPLE leaves the
  constraint unenforced when it is NULL, so org-wide configs are unaffected.

Combined with the forced RLS from 0003 (config row's ``organization_id`` must
equal the transaction context), the composite FK transitively pins every nested
reference inside the caller's own tenant.

Expand/contract discipline: ``downgrade`` restores the original single-column FK
and drops the helper unique constraint.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_nested_tenant_fk"
down_revision: str | None = "0003_rls_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORIGINAL_APP_FK = "fk_provider_configs_application_id_applications"
TENANT_APP_FK = "fk_provider_configs_application_org_applications"
APP_ID_ORG_UNIQUE = "uq_applications_id_org"


def upgrade() -> None:
    # 1. Composite FK target on applications (id, organization_id).
    op.create_unique_constraint(APP_ID_ORG_UNIQUE, "applications", ["id", "organization_id"])

    # 2. Replace the tenant-blind single-column pointer with a same-tenant pair.
    op.drop_constraint(ORIGINAL_APP_FK, "provider_configs", type_="foreignkey")
    op.create_foreign_key(
        TENANT_APP_FK,
        "provider_configs",
        "applications",
        ["application_id", "organization_id"],
        ["id", "organization_id"],
    )


def downgrade() -> None:
    op.drop_constraint(TENANT_APP_FK, "provider_configs", type_="foreignkey")
    op.create_foreign_key(
        ORIGINAL_APP_FK,
        "provider_configs",
        "applications",
        ["application_id"],
        ["id"],
    )
    op.drop_constraint(APP_ID_ORG_UNIQUE, "applications", type_="unique")
