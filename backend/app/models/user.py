"""Users and organization-scoped role assignments (four-role RBAC)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.auth.roles import ROLE_VALUES, Role
from app.core.time import new_id, utcnow
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.application import Application
    from app.models.tenant import Organization


class User(Base):
    """A human dashboard account belonging to exactly one organization.

    Authentication (Argon2 hashes, JWT sessions) arrives on Day 012; the
    ``password_hash`` column exists so Day 009's schema is already auth-ready
    and no migration ever has to rewrite identity rows.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(sa.String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    # Day 012 fills this with an Argon2id hash; never plaintext, never reused.
    password_hash: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        # Target for the composite cross-tenant FKs on role_assignments and
        # applications: (id, organization_id) must be a unique key.
        sa.UniqueConstraint("id", "organization_id", name="uq_users_id_org"),
    )

    organization: Mapped[Organization] = relationship(back_populates="users")
    role_assignments: Mapped[list[RoleAssignment]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    applications: Mapped[list[Application]] = relationship(
        "Application",
        primaryjoin="and_(User.id == foreign(Application.owner_user_id), "
        "User.organization_id == foreign(Application.organization_id))",
        viewonly=True,
        uselist=True,
    )

    @property
    def role(self) -> Role | None:
        """Convenience view of the user's single active role (None if unassigned)."""
        assignment = next(iter(self.role_assignments), None)
        return Role(assignment.role) if assignment is not None else None


class RoleAssignment(Base):
    """Grants one role to one user *inside one organization*.

    The composite foreign key ``(user_id, organization_id) -> users(id,
    organization_id)`` makes cross-tenant role grants structurally impossible:
    a row naming a user from tenant A can never claim tenant B, because no
    ``users`` row satisfies both ids at once — and organization *validity* is
    transitive through ``users.organization_id -> organizations.id`` (a column
    can hold only one FK, so the direct org link lives on the users row).
    RBAC is deliberately role-per-assignment (not per-user) so Day 012's JWT
    ``role`` claim has an unambiguous source.
    """

    __tablename__ = "role_assignments"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False, index=True)
    role: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        # Composite FK: (user, claimed org) must exist together in `users`,
        # so a grant can never straddle two tenants.
        sa.ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_role_assignments_user_org_users",
        ),
        sa.UniqueConstraint("user_id", name="uq_role_assignments_user"),
        sa.CheckConstraint(
            f"role IN ({', '.join(sorted(repr(value) for value in ROLE_VALUES))})",
            name="valid_role",
        ),
    )

    user: Mapped[User] = relationship(back_populates="role_assignments")

    @property
    def role_enum(self) -> Role:
        """Typed view of the stored role string; raises ValueError if corrupt."""
        return Role(self.role)
