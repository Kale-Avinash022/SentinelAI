"""Append-only audit records for security-relevant mutations (Day 013).

Every application/API-key change writes one of these rows **in the same
database transaction as the change itself** (see
:func:`app.services.audit.append_audit`): if the mutation rolls back, its
audit row rolls back with it; if the audit insert fails, the mutation is
thrown away too. There is no window where something happened that the trail
does not show.

Immutability is enforced by grants, not by convention: revision
``0006_api_keys_and_audit`` gives the API role ``SELECT`` and ``INSERT`` on
this table **and nothing else** — no ``UPDATE``, no ``DELETE``, not even on
its own rows. Retirement-by-expiry is left to a separate, future retention
role/worker (Day 061), never to the application identity.

Like every credential, rows are tenant-owned and RLS-forced. The
``detail_not_raw_key`` CHECK is a database-side guarantee that no plaintext
``sk_live_...`` secret can ever be written into the trail, even by a bug.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import new_id, utcnow
from app.models.base import Base


class AuditActorType(StrEnum):
    """Who caused the audited action.

    ``api_key`` actors arrive with the Week 3 gateway (machine-initiated
    changes); ``system`` covers future retention/automation workers.
    """

    USER = "user"
    API_KEY = "api_key"
    SYSTEM = "system"


AUDIT_ACTOR_TYPE_VALUES: frozenset[str] = frozenset(actor.value for actor in AuditActorType)


class AuditLog(Base):
    """One immutable security-relevant event inside one organization."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False, index=True
    )
    actor_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    # User id or api key id of the actor. Deliberately *not* a FK: an audit
    # row must outlive the credential it describes (keys keep working as
    # evidence after revocation; humans after deactivation).
    actor_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), nullable=True)
    # Stable dotted action name, e.g. "api_key.rotated" (app-level enum in
    # app.services.audit; validated there, free text here so old rows stay
    # readable when new actions land).
    action: Mapped[str] = mapped_column(sa.String(48), nullable=False)
    resource_type: Mapped[str] = mapped_column(sa.String(48), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False, index=True)
    # JSON *metadata* only (names, prefixes, scope lists, status flips).
    # Never prompt content, never a secret: the CHECK below rejects anything
    # carrying an sk_live_ marker outright.
    detail: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    # Correlation id of the request that caused the event (auditability of
    # the exact call, not just the actor).
    request_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    # NOTE: no updated_at on purpose — an append-only row never changes.

    __table_args__ = (
        sa.CheckConstraint(
            f"actor_type IN ({', '.join(sorted(repr(value) for value in AUDIT_ACTOR_TYPE_VALUES))})",
            name="valid_actor_type",
        ),
        sa.CheckConstraint(
            "detail IS NULL OR detail NOT LIKE '%sk_live_%'",
            name="detail_not_raw_key",
        ),
    )


__all__ = ["AUDIT_ACTOR_TYPE_VALUES", "AuditActorType", "AuditLog"]
