"""Persistence port for authentication (Day 012).

Defines the narrow storage contract the auth endpoints depend on — identity
lookup, authoritative membership resolution and refresh-token rotation — as a
:class:`typing.Protocol`, plus the production PostgreSQL implementation.

Every tenant-scoped operation runs inside :func:`tenant_session_scope`, so the
RLS context is always set before a query and discarded when the transaction
ends. The one context-free read (login by email) goes through the reviewed
``SECURITY DEFINER`` lookup function created in revision ``0005_refresh_sessions``
— the *only* sanctioned way to resolve credentials before an organization is
known. The in-memory fake used by contract tests implements the same protocol
without a database.
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.time import utcnow
from app.infrastructure.database import tenant_session_scope
from app.models.session import RefreshSession
from app.models.user import User


@dataclass(frozen=True, slots=True)
class LoginIdentity:
    """The four login-critical fields resolved from a globally-unique email."""

    user_id: uuid.UUID
    organization_id: uuid.UUID
    password_hash: str | None
    is_active: bool


@dataclass(frozen=True, slots=True)
class Membership:
    """Server-side truth of a user's role within one organization.

    The RBAC dependency reads *this*, never the JWT ``role`` claim alone, so a
    token cannot keep a privilege the database has already withdrawn.
    """

    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str
    is_active: bool


class RotateStatus(enum.Enum):
    """Outcome of attempting to redeem a refresh token and rotate it."""

    ROTATED = "rotated"
    REUSED = "reused"  # presented a token already rotated/revoked -> theft
    UNKNOWN = "unknown"  # no such session row in this tenant


@dataclass(frozen=True, slots=True)
class RotateResult:
    status: RotateStatus
    family_id: uuid.UUID | None = None


class AuthRepository(Protocol):
    """Storage contract consumed by the auth endpoints."""

    async def lookup_login_identity(self, email: str) -> LoginIdentity | None: ...

    async def get_membership(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Membership | None: ...

    async def update_password_hash(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID, password_hash: str
    ) -> None: ...

    async def create_session(
        self,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        family_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> None: ...

    async def rotate_session(
        self,
        *,
        organization_id: uuid.UUID,
        presented_hash: str,
        new_hash: str,
        new_expires_at: datetime,
    ) -> RotateResult: ...

    async def revoke_family(self, *, organization_id: uuid.UUID, family_id: uuid.UUID) -> None: ...


class PostgresAuthRepository:
    """:class:`AuthRepository` backed by the runtime ``sentinel_app`` role.

    Holds only a session factory; it never opens a connection at construction.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def _tx(self, organization_id: uuid.UUID | None = None) -> AsyncIterator[AsyncSession]:
        """One tenant-scoped transaction (or a context-free one when ``None``)."""
        if organization_id is None:
            async with _plain_scope(self._session_factory) as session:
                yield session
        else:
            async with tenant_session_scope(self._session_factory, organization_id) as session:
                yield session

    async def lookup_login_identity(self, email: str) -> LoginIdentity | None:
        # Context-free on purpose: RLS would hide every users row here. The
        # SECURITY DEFINER function is the single reviewed exception.
        async with self._tx(None) as session:
            row = (
                await session.execute(
                    sa.text(
                        "SELECT user_id, organization_id, password_hash, is_active "
                        "FROM app_lookup_login_identity(:email)"
                    ),
                    {"email": email},
                )
            ).first()
        if row is None:
            return None
        return LoginIdentity(
            user_id=_coerce_uuid(row.user_id),
            organization_id=_coerce_uuid(row.organization_id),
            password_hash=row.password_hash,
            is_active=bool(row.is_active),
        )

    async def get_membership(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Membership | None:
        async with self._tx(organization_id) as session:
            row = (
                await session.execute(
                    sa.text(
                        "SELECT u.id, u.organization_id, r.role, u.is_active "
                        "FROM users u JOIN role_assignments r "
                        "ON r.user_id = u.id AND r.organization_id = u.organization_id "
                        "WHERE u.id = :user_id"
                    ),
                    {"user_id": user_id},
                )
            ).first()
        if row is None:
            return None
        return Membership(
            user_id=_coerce_uuid(row.id),
            organization_id=_coerce_uuid(row.organization_id),
            role=str(row.role),
            is_active=bool(row.is_active),
        )

    async def update_password_hash(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID, password_hash: str
    ) -> None:
        # Rehash-on-login migration to current Argon2 costs. Runs inside the
        # user's tenant context so RLS WITH CHECK accepts the update.
        async with self._tx(organization_id) as session:
            await session.execute(
                sa.update(User)
                .where(User.id == user_id, User.organization_id == organization_id)
                .values(password_hash=password_hash, updated_at=utcnow())
            )

    async def create_session(
        self,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        family_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> None:
        async with self._tx(organization_id) as session:
            session.add(
                RefreshSession(
                    organization_id=organization_id,
                    user_id=user_id,
                    family_id=family_id,
                    token_hash=token_hash,
                    expires_at=expires_at,
                )
            )

    async def rotate_session(
        self,
        *,
        organization_id: uuid.UUID,
        presented_hash: str,
        new_hash: str,
        new_expires_at: datetime,
    ) -> RotateResult:
        """Atomically redeem one refresh token and issue its successor.

        Within a single tenant transaction: locate the presented row, refuse a
        replay (already revoked -> theft), retire it, and insert the successor in
        the same family. Returns :attr:`RotateStatus.ROTATED`, or ``REUSED``
        (after revoking the whole family), or ``UNKNOWN`` (no matching row).
        """
        async with self._tx(organization_id) as session:
            existing = (
                await session.execute(
                    sa.select(RefreshSession).where(RefreshSession.token_hash == presented_hash)
                )
            ).scalar_one_or_none()
            if existing is None:
                return RotateResult(RotateStatus.UNKNOWN)
            if existing.revoked_at is not None:
                # Reuse of a rotated token: burn the entire family.
                family = existing.family_id
                await session.execute(
                    sa.update(RefreshSession)
                    .where(
                        RefreshSession.family_id == family,
                        RefreshSession.revoked_at.is_(None),
                    )
                    .values(revoked_at=utcnow())
                )
                return RotateResult(RotateStatus.REUSED, family_id=family)

            existing.revoked_at = utcnow()
            family = existing.family_id
            session.add(
                RefreshSession(
                    organization_id=organization_id,
                    user_id=existing.user_id,
                    family_id=family,
                    token_hash=new_hash,
                    expires_at=new_expires_at,
                )
            )
            return RotateResult(RotateStatus.ROTATED, family_id=family)

    async def revoke_family(self, *, organization_id: uuid.UUID, family_id: uuid.UUID) -> None:
        async with self._tx(organization_id) as session:
            await session.execute(
                sa.update(RefreshSession)
                .where(
                    RefreshSession.family_id == family_id,
                    RefreshSession.revoked_at.is_(None),
                )
                .values(revoked_at=utcnow())
            )


@asynccontextmanager
async def _plain_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A normal (non-tenant) transaction used only by the definer lookup."""
    session = factory()
    try:
        async with session.begin():
            yield session
    finally:
        await session.close()


def _coerce_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


__all__ = [
    "AuthRepository",
    "LoginIdentity",
    "Membership",
    "PostgresAuthRepository",
    "RotateResult",
    "RotateStatus",
]
