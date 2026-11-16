"""Gateway-facing applications owned by a user inside one organization."""

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
    from app.models.provider_catalog import ProviderConfig
    from app.models.tenant import Organization
    from app.models.user import User


class ApplicationStatus(StrEnum):
    """Lifecycle of a gateway application."""

    ACTIVE = "active"
    ARCHIVED = "archived"


APPLICATION_STATUS_VALUES: frozenset[str] = frozenset(status.value for status in ApplicationStatus)


class Application(Base):
    """An API-key-bearing workload (brief §4 "Applications").

    Ownership is double-bound: ``organization_id`` places the app inside a
    tenant and the composite foreign key
    ``(owner_user_id, organization_id) -> users(id, organization_id)``
    guarantees the owner belongs to *that same* tenant — an app can never be
    owned by a user from another organization, at the schema level, not just
    by application convention.
    """

    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False, index=True
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default=ApplicationStatus.ACTIVE.value
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["owner_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_applications_owner_user_org_users",
        ),
        sa.UniqueConstraint("organization_id", "name", name="uq_applications_org_name"),
        sa.CheckConstraint(
            f"status IN ({', '.join(sorted(repr(value) for value in APPLICATION_STATUS_VALUES))})",
            name="valid_application_status",
        ),
    )

    organization: Mapped[Organization] = relationship(back_populates="applications")
    owner: Mapped[User] = relationship(
        "User",
        primaryjoin="and_(foreign(Application.owner_user_id) == User.id, "
        "foreign(Application.organization_id) == User.organization_id)",
        viewonly=True,
        uselist=False,
    )
    provider_configs: Mapped[list[ProviderConfig]] = relationship(
        "ProviderConfig", back_populates="application", cascade="all, delete-orphan"
    )
