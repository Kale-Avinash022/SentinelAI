"""immutable config/prompt versions + CAS activation history: Day 014

Revision ID: 0007_config_prompt_versions
Revises: 0006_api_keys_and_audit
Create Date: 2026-11-21

Day 014 adds the configuration history that makes rollback meaningful — every
change attributable, immutable and compatibility-checked — and moves prompt
bodies out of the clear. Four deliberate database facts:

* ``config_versions`` — one row per point in a ``(kind, name)`` ladder, with
  **no** ``updated_at`` and grants of exactly ``SELECT`` + ``INSERT``.
  "Historical versions cannot be edited in place" is therefore a privilege
  fact, not a convention: there is no writable path. CHECKs keep secrets out of
  the non-secret ``payload`` column, require a prompt template to be either
  sealed (``encrypted_payload``) or externally referenced (``secret_ref``
  locator, same shape rule as ``provider_credentials``), and forbid a
  provider-route version from carrying sensitive material at all.
* ``config_activations`` — append-only activation history that doubles as the
  live pointer. The **partial unique index** ``(organization_id, kind, name)
  WHERE superseded_at IS NULL`` is the compare-and-swap guarantee: two
  concurrent activations of the same slot conflict on the database instead of
  silently overwriting each other. Grants are ``SELECT``, ``INSERT`` and
  column-level ``UPDATE (superseded_at)`` only — a record may be *closed*,
  never rewritten, and never deleted. ``ck_..._lkg_requires_evidence`` makes
  last-known-good require explicit qualification rather than mere precedence.
* Composite ``(pointer, organization_id)`` foreign keys bind actor, referenced
  version and superseded activation to their own tenant (the Day 011 rule that
  a naked FK can resolve rows RLS is meant to hide), so an activation can never
  point at another organization's version.
* ``audit_logs`` — the trail's secret guard widens from "no raw API key" to
  "no raw API key **and no sealed payload**": once bodies are encrypted at
  rest, a ciphertext blob is just as much secret material as ``sk_live_``, and
  neither may ever be written into history.

Expand/contract discipline: additive ``upgrade`` (plus the reviewed audit CHECK
swap) with a full reverse ``downgrade``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_config_prompt_versions"
down_revision: str | None = "0006_api_keys_and_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "sentinel_app"
VERSIONS_TABLE = "config_versions"
VERSIONS_POLICY = "config_versions_tenant_isolation"
ACTIVATIONS_TABLE = "config_activations"
ACTIVATIONS_POLICY = "config_activations_tenant_isolation"
AUDIT_TABLE = "audit_logs"
TENANT_GUC = "app.organization_id"

OLD_AUDIT_CHECK = "ck_audit_logs_detail_not_raw_key"
NEW_AUDIT_CHECK = "ck_audit_logs_detail_no_secret_material"
# Fernet tokens are base64 and always start with the version byte 0x80, which
# encodes as "gAAAAA"; "fernet:" is this project's own sealed-payload prefix.
# Both markers together mean "a sealed body is being written into the trail".
AUDIT_CHECK_EXPRESSION = (
    "detail IS NULL OR (detail NOT LIKE '%sk_live_%' "
    "AND detail NOT LIKE '%gAAAAA%' AND detail NOT LIKE '%fernet:%')"
)

#: Mirrors ``app.configs.rules.CONFIG_KIND_VALUES`` exactly (adding a kind is a
#: reviewed migration, never a free-form client string).
KIND_CHECK_VALUES = "'prompt', 'provider_route'"
SECRET_REF_PREFIX = "secret://"


def _tenant_predicate() -> str:
    # Identical shape to revisions 0003/0005/0006: NULLIF collapses the
    # never-set NULL and the post-revert empty string to NULL, which matches no
    # organization, so missing context denies instead of broadening.
    return f"organization_id = NULLIF(current_setting('{TENANT_GUC}', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        VERSIONS_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("secret_ref", sa.String(length=500), nullable=True),
        sa.Column("encrypted_payload", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"kind IN ({KIND_CHECK_VALUES})",
            name=op.f("ck_config_versions_valid_kind"),
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name=op.f("ck_config_versions_positive_version_number"),
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64",
            name=op.f("ck_config_versions_valid_content_hash_length"),
        ),
        sa.CheckConstraint(
            "payload <> ''",
            name=op.f("ck_config_versions_non_empty_payload"),
        ),
        sa.CheckConstraint(
            "secret_ref IS NULL OR length(secret_ref) > 0",
            name=op.f("ck_config_versions_secret_ref_not_blank"),
        ),
        sa.CheckConstraint(
            f"secret_ref IS NULL OR (secret_ref LIKE '{SECRET_REF_PREFIX}%' "
            "AND secret_ref NOT LIKE '%{%')",
            name=op.f("ck_config_versions_secret_ref_is_locator_not_payload"),
        ),
        sa.CheckConstraint(
            "encrypted_payload IS NULL OR length(encrypted_payload) > 0",
            name=op.f("ck_config_versions_encrypted_payload_not_blank"),
        ),
        sa.CheckConstraint(
            "payload NOT LIKE '%sk_live_%' AND payload NOT LIKE '%api_key%' "
            "AND payload NOT LIKE '%apikey%' AND payload NOT LIKE '%password%' "
            "AND payload NOT LIKE '%secret://%' AND payload NOT LIKE '%gAAAAA%'",
            name=op.f("ck_config_versions_payload_carries_no_credential"),
        ),
        sa.CheckConstraint(
            "kind <> 'prompt' OR secret_ref IS NOT NULL OR encrypted_payload IS NOT NULL",
            name=op.f("ck_config_versions_prompt_body_is_sealed_or_referenced"),
        ),
        sa.CheckConstraint(
            "kind <> 'provider_route' OR (secret_ref IS NULL AND encrypted_payload IS NULL)",
            name=op.f("ck_config_versions_route_version_has_no_sensitive_body"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name=op.f("fk_config_versions_created_by_user_org_users"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_config_versions_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_config_versions")),
        sa.UniqueConstraint(
            "organization_id",
            "kind",
            "name",
            "version_number",
            name=op.f("uq_config_versions_org_kind_name_number"),
        ),
        sa.UniqueConstraint("id", "organization_id", name=op.f("uq_config_versions_id_org")),
    )
    op.create_index(
        op.f("ix_config_versions_organization_id"),
        VERSIONS_TABLE,
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_config_versions_slot",
        VERSIONS_TABLE,
        ["organization_id", "kind", "name", "version_number"],
        unique=False,
    )

    op.create_table(
        ACTIVATIONS_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("expected_previous_number", sa.Integer(), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("activated_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qualification", sa.Text(), nullable=True),
        sa.Column("is_last_known_good", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            f"kind IN ({KIND_CHECK_VALUES})",
            name=op.f("ck_config_activations_valid_kind"),
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name=op.f("ck_config_activations_positive_version_number"),
        ),
        sa.CheckConstraint(
            "expected_previous_number IS NULL OR expected_previous_number >= 0",
            name=op.f("ck_config_activations_valid_expected_previous_number"),
        ),
        sa.CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id",
            name=op.f("ck_config_activations_no_self_supersession"),
        ),
        sa.CheckConstraint(
            "is_last_known_good = FALSE OR qualification IS NOT NULL",
            name=op.f("ck_config_activations_lkg_requires_evidence"),
        ),
        sa.CheckConstraint(
            "qualification IS NULL OR (qualification NOT LIKE '%sk_live_%' "
            "AND qualification NOT LIKE '%gAAAAA%' AND qualification NOT LIKE '%fernet:%')",
            name=op.f("ck_config_activations_qualification_carries_no_secret"),
        ),
        sa.ForeignKeyConstraint(
            ["activated_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name=op.f("fk_config_activations_activated_by_user_org_users"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_config_activations_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id", "organization_id"],
            ["config_activations.id", "config_activations.organization_id"],
            name=op.f("fk_config_activations_supersedes_org_config_activations"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id", "organization_id"],
            ["config_versions.id", "config_versions.organization_id"],
            name=op.f("fk_config_activations_version_org_config_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_config_activations")),
        sa.UniqueConstraint("id", "organization_id", name=op.f("uq_config_activations_id_org")),
    )
    op.create_index(
        op.f("ix_config_activations_organization_id"),
        ACTIVATIONS_TABLE,
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_config_activations_version_id"), ACTIVATIONS_TABLE, ["version_id"], unique=False
    )
    op.create_index(
        "ix_config_activations_slot",
        ACTIVATIONS_TABLE,
        ["organization_id", "kind", "name"],
        unique=False,
    )
    # THE compare-and-swap invariant: one live activation per tenant/slot. A
    # second concurrent activation of the same slot raises a unique violation
    # instead of quietly becoming a second source of truth.
    op.create_index(
        "uq_config_activations_live_slot",
        ACTIVATIONS_TABLE,
        ["organization_id", "kind", "name"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )

    # Least-privilege DML.
    #   config_versions:   SELECT + INSERT only -> history is immutable.
    #   config_activations: SELECT + INSERT + UPDATE(superseded_at) -> a record
    #   can be closed, never rewritten, and never deleted.
    op.execute(f"REVOKE ALL ON TABLE {VERSIONS_TABLE} FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE {VERSIONS_TABLE} TO {APP_ROLE}")
    op.execute(f"REVOKE ALL ON TABLE {ACTIVATIONS_TABLE} FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE {ACTIVATIONS_TABLE} TO {APP_ROLE}")
    op.execute(f"GRANT UPDATE (superseded_at) ON TABLE {ACTIVATIONS_TABLE} TO {APP_ROLE}")

    # Same forced, permissive, all-role tenant-isolation policy as 0003/0005/0006.
    predicate = _tenant_predicate()
    for table, policy in (
        (VERSIONS_TABLE, VERSIONS_POLICY),
        (ACTIVATIONS_TABLE, ACTIVATIONS_POLICY),
    ):
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"CREATE POLICY {policy} ON {table} "
            f"AS PERMISSIVE FOR ALL "
            f"USING ({predicate}) "
            f"WITH CHECK ({predicate})"
        )

    # Widen the audit trail's secret guard now that sealed bodies exist: no raw
    # API key, and no ciphertext either. Both halves are enforced in Python
    # (app.services.audit) and repeated here as a database fact.
    op.execute(f"ALTER TABLE {AUDIT_TABLE} DROP CONSTRAINT {OLD_AUDIT_CHECK}")
    op.execute(
        f"ALTER TABLE {AUDIT_TABLE} ADD CONSTRAINT {NEW_AUDIT_CHECK} "
        f"CHECK ({AUDIT_CHECK_EXPRESSION})"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {AUDIT_TABLE} DROP CONSTRAINT {NEW_AUDIT_CHECK}")
    op.execute(
        f"ALTER TABLE {AUDIT_TABLE} ADD CONSTRAINT {OLD_AUDIT_CHECK} "
        "CHECK (detail IS NULL OR detail NOT LIKE '%sk_live_%')"
    )
    for table, policy in (
        (ACTIVATIONS_TABLE, ACTIVATIONS_POLICY),
        (VERSIONS_TABLE, VERSIONS_POLICY),
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
        op.execute(f"REVOKE ALL ON TABLE {table} FROM {APP_ROLE}")
    op.drop_index("uq_config_activations_live_slot", table_name=ACTIVATIONS_TABLE)
    op.drop_index("ix_config_activations_slot", table_name=ACTIVATIONS_TABLE)
    op.drop_index(op.f("ix_config_activations_version_id"), table_name=ACTIVATIONS_TABLE)
    op.drop_index(op.f("ix_config_activations_organization_id"), table_name=ACTIVATIONS_TABLE)
    op.drop_table(ACTIVATIONS_TABLE)
    op.drop_index("ix_config_versions_slot", table_name=VERSIONS_TABLE)
    op.drop_index(op.f("ix_config_versions_organization_id"), table_name=VERSIONS_TABLE)
    op.drop_table(VERSIONS_TABLE)
