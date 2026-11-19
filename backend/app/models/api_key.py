"""Hashed, scoped machine credentials for gateway applications (Day 013).

A key is issued as ``sk_live_<40 hex chars>``: the secret half is 128 bits of
cryptographically secure randomness, and **only its SHA-256 digest plus a short
display prefix are ever stored** — the raw value exists once, in the create
response. Like refresh tokens (Day 012), the preimage is uniformly random and
high-entropy, so a fast cryptographic hash (not a slow KDF) is the correct
irreversible commitment.

Tenant binding follows the Day 009/011 rule: ``organization_id`` places the
row inside a tenant and *composite* foreign keys bind pointer+tenant together,
so a key can never name another organization's application, another
organization's creating user, or another organization's predecessor key.
Revocation is an ``UPDATE`` (``revoked_at``), never a ``DELETE`` — the row
stays as evidence, which is why the API role holds no DELETE grant here.

Rotation is deliberate two-key overlap: the predecessor keeps working until a
capped ``expires_at`` (``superseded_by_id`` records lineage), then dies by
expiry; an explicit revoke kills any key immediately.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import new_id, utcnow
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.application import Application
    from app.models.user import User


class KeyScope(StrEnum):
    """The closed set of capabilities a machine key may carry.

    Deliberately narrow in Week 2: gateway invocation arrives Day 015+ and
    telemetry reads Day 031. Adding a scope here is a reviewed change to the
    CHECK constraint, not a free-form string clients may invent.
    """

    GATEWAY_INVOKE = "gateway:invoke"
    TELEMETRY_READ = "telemetry:read"


KEY_SCOPE_VALUES: frozenset[str] = frozenset(scope.value for scope in KeyScope)


class ApiKey(Base):
    """One machine credential belonging to exactly one application.

    ``secret_hash`` is unique: a presented raw key maps to at most one row
    anywhere in the database, which is what lets the reviewed
    ``SECURITY DEFINER`` lookup resolve the owning organization *from the
    stored record itself* (never from a client-supplied header).
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False, index=True
    )
    application_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False, index=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    # Public identifier fragment ("sk_live_" + first secret chars). Safe to
    # list; useful for support. The remainder of the secret is only hashed.
    key_prefix: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    # SHA-256 hex of the full raw key. Never the key itself.
    secret_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    # Canonical "a,b" scope list, validated against KEY_SCOPE_VALUES in code.
    scopes: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    # Scheduled death (explicit expiry or the end of a rotation overlap).
    # NULL means the key does not expire on its own.
    expires_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True, index=True
    )
    # Immediate revocation marker; setting it kills the key right now.
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    # Set when this key was rotated into a successor (lineage, not usability).
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        # Pointer + tenant bound together (Day 011 rule): a key can never
        # attach to another organization's application, user, or predecessor.
        sa.ForeignKeyConstraint(
            ["application_id", "organization_id"],
            ["applications.id", "applications.organization_id"],
            name="fk_api_keys_application_org_applications",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_api_keys_created_by_user_org_users",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id", "organization_id"],
            ["api_keys.id", "api_keys.organization_id"],
            name="fk_api_keys_superseded_by_org_api_keys",
        ),
        # Composite FK target for the self-referencing rotation lineage above.
        sa.UniqueConstraint("id", "organization_id", name="uq_api_keys_id_org"),
        # One raw key digest maps to at most one row across the whole DB.
        sa.UniqueConstraint("secret_hash", name="uq_api_keys_secret_hash"),
        sa.CheckConstraint(
            "key_prefix LIKE 'sk_live_%'",
            name="valid_key_prefix",
        ),
        sa.CheckConstraint("length(secret_hash) = 64", name="valid_secret_hash_length"),
        sa.CheckConstraint("scopes <> ''", name="non_empty_scopes"),
        sa.CheckConstraint(
            "superseded_by_id IS NULL OR superseded_by_id <> id",
            name="no_self_supersession",
        ),
    )

    # Relationships stay viewonly+foreign() like every composite-FK pair since
    # Day 011: the ORM cannot manage dual-bound tenant guards automatically.
    application: Mapped[Application] = relationship(
        "Application",
        primaryjoin="and_(foreign(ApiKey.application_id) == Application.id, "
        "foreign(ApiKey.organization_id) == Application.organization_id)",
        viewonly=True,
        uselist=False,
    )
    created_by: Mapped[User] = relationship(
        "User",
        primaryjoin="and_(foreign(ApiKey.created_by_user_id) == User.id, "
        "foreign(ApiKey.organization_id) == User.organization_id)",
        viewonly=True,
        uselist=False,
    )


__all__ = ["KEY_SCOPE_VALUES", "ApiKey", "KeyScope"]
