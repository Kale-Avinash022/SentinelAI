"""Organization (tenant) root model — every tenant-owned row carries its id."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import new_id, utcnow
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.application import Application
    from app.models.provider_catalog import ProviderConfig, ProviderCredential
    from app.models.user import User


class Organization(Base):
    """A tenant boundary. RLS (Day 010) filters every tenant table on this id.

    ``allow_self_approval`` is the configurable, **default-deny** switch for
    same-person request/approve on high-risk changes (see
    :func:`app.auth.permissions.self_approval_allowed` and ADR-0004). It only
    ever unlocks Category B self-approval by an Owner; Category C stays
    manual-only no matter what this flag says.
    """

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    slug: Mapped[str] = mapped_column(sa.String(63), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)
    allow_self_approval: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    users: Mapped[list[User]] = relationship(
        "User", back_populates="organization", cascade="all, delete-orphan"
    )
    applications: Mapped[list[Application]] = relationship(
        "Application", back_populates="organization", cascade="all, delete-orphan"
    )
    provider_configs: Mapped[list[ProviderConfig]] = relationship(
        "ProviderConfig", back_populates="organization", cascade="all, delete-orphan"
    )
    provider_credentials: Mapped[list[ProviderCredential]] = relationship(
        "ProviderCredential", back_populates="organization", cascade="all, delete-orphan"
    )
