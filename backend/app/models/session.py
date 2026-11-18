"""Rotating, revocable refresh-session records (Day 012).

Only a **hash** of each refresh token is ever persisted — never the token
itself — so a database dump cannot be replayed against the API. Rows are
tenant-owned (RLS-filtered on ``organization_id``) and bound to their user by
the same composite ``(user_id, organization_id) -> users`` foreign key used by
``role_assignments`` and ``applications``, which makes a cross-tenant session
row structurally impossible.

Rotation model: every refresh mints a *new* row in the same ``family_id`` and
marks the presented row revoked (single use). Presenting an already-revoked
token is treated as theft and revokes the entire family. Access tokens are
never stored — they are validated purely by signature/expiry.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import new_id, utcnow
from app.models.base import Base


class RefreshSession(Base):
    """One live-or-revoked refresh token digest within a rotation family."""

    __tablename__ = "refresh_sessions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False, index=True)
    # SHA-256 (hex) of the raw refresh JWT. Unique so a token maps to one row.
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    # Rotation family shared by every descendant token of one login.
    family_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, index=True
    )
    # Non-null once the token has been rotated away, logged out, or force-revoked.
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        # Composite FK: a session's (user, org) pair must exist in ``users``, so
        # a refresh row can never name a user from a different organization.
        sa.ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_refresh_sessions_user_org_users",
        ),
        sa.CheckConstraint("length(token_hash) = 64", name="valid_token_hash_length"),
    )
