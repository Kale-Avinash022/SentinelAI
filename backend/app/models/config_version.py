"""Immutable configuration & prompt version history with activation records (Day 014).

Rollback is only meaningful when every change is **attributable, immutable and
compatible**, so this module pushes all three into the schema instead of
trusting application code:

* :class:`ConfigVersion` — one point in a ``(kind, name)`` version ladder.
  There is deliberately **no ``updated_at``** and the API role holds
  ``SELECT``/``INSERT`` only (revision ``0007``): a historical version cannot be
  edited in place, not "should not", because no writable path exists.
* Sensitive material never sits in the metadata column. ``payload`` is
  non-secret JSON (capabilities, allowed providers, privacy class, quality
  floor, timeout/budget limits, evaluator version, prompt reference); the prompt
  template itself is either ``encrypted_payload`` (sealed by
  :class:`~app.configs.vault.FernetVault`) or an external ``secret_ref`` locator.
  CHECKs enforce the locator shape, ban credential-shaped keys inside
  ``payload``, and require a prompt version to have one or the other.
* :class:`ConfigActivation` — the append-only activation history and the single
  live pointer. A **partial unique index** on ``(organization_id, kind, name)
  WHERE superseded_at IS NULL`` makes "one active version per slot" a database
  fact, which is what turns activation into compare-and-swap: a concurrent
  change conflicts (unique violation / zero-row close) instead of silently
  overwriting. The only column the API role may UPDATE is ``superseded_at``
  (closing a record), so history cannot be rewritten either.
* **Last-known-good is earned, not inherited.** ``ck_config_activations_
  lkg_requires_evidence`` refuses a row flagged ``is_last_known_good`` without a
  ``qualification`` payload, so "the previous row" can never be mistaken for
  "a version that passed validation/evaluation".

Tenant binding follows Day 009/011: ``organization_id`` plus *composite*
(pointer, organization) foreign keys for the actor, the referenced version and
the superseded activation, so a version can never point at another
organization's prompt or activation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.configs.rules import CONFIG_KIND_VALUES, PROMPT, PROVIDER_ROUTE
from app.core.time import new_id, utcnow
from app.models.base import Base
from app.models.provider_catalog import SECRET_REF_PREFIX

if TYPE_CHECKING:
    from app.models.user import User

#: Values mirrored by ``ck_*_valid_kind`` on both tables.
KIND_VALUES: frozenset[str] = CONFIG_KIND_VALUES

_KIND_CHECK_VALUES = ", ".join(sorted(repr(value) for value in sorted(KIND_VALUES)))


class ConfigVersion(Base):
    """One immutable version of a provider-route or prompt configuration.

    ``content_hash`` addresses the logical content (metadata + secret reference
    + plaintext body), so two operators who disagree about a version can prove
    equality or difference without either revealing a template.
    """

    __tablename__ = "config_versions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    # Slot identity within the organization: the ladder this version belongs to
    # ("support-chat-route", "triage-system-prompt"). Immutable like the rest.
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    # 1-based position in the ladder, assigned by the store under the slot's
    # UNIQUE guard — never supplied by a client.
    version_number: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    content_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    # Non-secret metadata as canonical JSON text (same storage shape as
    # ``audit_logs.detail``, which keeps the CHECKs portable and the DDL stable).
    payload: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    # External secret locator for a body kept outside the database (Day 008
    # rule: a reference, never a payload).
    secret_ref: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    # Fernet-sealed prompt template. Plaintext never appears in this column, in
    # a log line, or in the audit trail.
    encrypted_payload: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )
    # NOTE: no updated_at on purpose — an immutable row never changes.

    __table_args__ = (
        sa.CheckConstraint(f"kind IN ({_KIND_CHECK_VALUES})", name="valid_kind"),
        sa.CheckConstraint("version_number > 0", name="positive_version_number"),
        sa.CheckConstraint("length(content_hash) = 64", name="valid_content_hash_length"),
        sa.CheckConstraint("payload <> ''", name="non_empty_payload"),
        sa.CheckConstraint(
            "secret_ref IS NULL OR length(secret_ref) > 0", name="secret_ref_not_blank"
        ),
        # Same locator rule as provider_credentials: a reference points at a
        # secret, it does not carry one.
        sa.CheckConstraint(
            f"secret_ref IS NULL OR (secret_ref LIKE '{SECRET_REF_PREFIX}%' "
            "AND secret_ref NOT LIKE '%{%')",
            name="secret_ref_is_locator_not_payload",
        ),
        sa.CheckConstraint(
            "encrypted_payload IS NULL OR length(encrypted_payload) > 0",
            name="encrypted_payload_not_blank",
        ),
        # Credentials stay external: the metadata column may not smuggle a key,
        # a password field, a raw API-key marker or a sealed blob.
        sa.CheckConstraint(
            "payload NOT LIKE '%sk_live_%' AND payload NOT LIKE '%api_key%' "
            "AND payload NOT LIKE '%apikey%' AND payload NOT LIKE '%password%' "
            "AND payload NOT LIKE '%secret://%' AND payload NOT LIKE '%gAAAAA%'",
            name="payload_carries_no_credential",
        ),
        # A prompt template is either encrypted at rest or referenced outside —
        # never absent (unattributable content) and never inline in metadata.
        sa.CheckConstraint(
            f"kind <> '{PROMPT}' OR secret_ref IS NOT NULL OR encrypted_payload IS NOT NULL",
            name="prompt_body_is_sealed_or_referenced",
        ),
        sa.CheckConstraint(
            f"kind <> '{PROVIDER_ROUTE}' OR (secret_ref IS NULL AND encrypted_payload IS NULL)",
            name="route_version_has_no_sensitive_body",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_config_versions_created_by_user_org_users",
        ),
        # The version ladder: one row per (tenant, kind, slot, number).
        sa.UniqueConstraint(
            "organization_id",
            "kind",
            "name",
            "version_number",
            name="uq_config_versions_org_kind_name_number",
        ),
        # Composite FK target for activation pointers below.
        sa.UniqueConstraint("id", "organization_id", name="uq_config_versions_id_org"),
    )

    created_by: Mapped[User] = relationship(
        "User",
        primaryjoin="and_(foreign(ConfigVersion.created_by_user_id) == User.id, "
        "foreign(ConfigVersion.organization_id) == User.organization_id)",
        viewonly=True,
        uselist=False,
    )


class ConfigActivation(Base):
    """One activation of ``version_id`` in slot ``(kind, name)``, plus history.

    The row is *both* the audit of a switch and the live pointer: while
    ``superseded_at`` IS NULL it is the slot's active version, and the partial
    unique index guarantees no second one can open concurrently. Closing it is
    the sole permitted UPDATE.
    """

    __tablename__ = "config_activations"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False, index=True)
    # Denormalized copy of the version's number so the compare-and-swap
    # predicate and the conflict message never need a second round trip.
    version_number: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    # What the writer believed was active (NULL = "nothing was active yet").
    # Recorded, not enforced: the authoritative conflict detection is the close
    # predicate plus the partial unique index.
    expected_previous_number: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), nullable=True)
    activated_by_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )
    # Set exactly once, when the successor opens. NULL means "this is active".
    superseded_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    # Explicit qualification evidence (evaluation run, scores, approver notes).
    # Required to count as last-known-good; JSON text, secret-free by convention
    # and by the CHECK below.
    qualification: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    is_last_known_good: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)

    __table_args__ = (
        sa.CheckConstraint(f"kind IN ({_KIND_CHECK_VALUES})", name="valid_kind"),
        sa.CheckConstraint("version_number > 0", name="positive_version_number"),
        sa.CheckConstraint(
            "expected_previous_number IS NULL OR expected_previous_number >= 0",
            name="valid_expected_previous_number",
        ),
        sa.CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id",
            name="no_self_supersession",
        ),
        # Last-known-good must be *earned*: a version only qualifies with
        # explicit evidence, never merely by having existed before.
        sa.CheckConstraint(
            "is_last_known_good = FALSE OR qualification IS NOT NULL",
            name="lkg_requires_evidence",
        ),
        sa.CheckConstraint(
            "qualification IS NULL OR (qualification NOT LIKE '%sk_live_%' "
            "AND qualification NOT LIKE '%gAAAAA%' AND qualification NOT LIKE '%fernet:%')",
            name="qualification_carries_no_secret",
        ),
        sa.ForeignKeyConstraint(
            ["version_id", "organization_id"],
            ["config_versions.id", "config_versions.organization_id"],
            name="fk_config_activations_version_org_config_versions",
        ),
        sa.ForeignKeyConstraint(
            ["activated_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_config_activations_activated_by_user_org_users",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id", "organization_id"],
            ["config_activations.id", "config_activations.organization_id"],
            name="fk_config_activations_supersedes_org_config_activations",
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_config_activations_id_org"),
        # THE compare-and-swap guarantee: at most one live activation per slot.
        sa.Index(
            "uq_config_activations_live_slot",
            "organization_id",
            "kind",
            "name",
            unique=True,
            postgresql_where=sa.text("superseded_at IS NULL"),
            sqlite_where=sa.text("superseded_at IS NULL"),
        ),
        sa.Index("ix_config_activations_slot", "organization_id", "kind", "name"),
    )

    version: Mapped[ConfigVersion] = relationship(
        "ConfigVersion",
        primaryjoin="and_(foreign(ConfigActivation.version_id) == ConfigVersion.id, "
        "foreign(ConfigActivation.organization_id) == ConfigVersion.organization_id)",
        viewonly=True,
        uselist=False,
    )


__all__ = [
    "KIND_VALUES",
    "ConfigActivation",
    "ConfigVersion",
]
