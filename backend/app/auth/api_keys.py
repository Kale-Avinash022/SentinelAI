"""Machine-credential resolution for later scoped endpoints (Day 013).

A presented raw key must be resolved to its owning organization **before any
tenant context exists** — the same chicken-and-egg Day 012 solved for human
login, and solved the same reviewed way: the ``SECURITY DEFINER``
``app_lookup_api_key(text)`` function from revision ``0006`` is the only
context-free read, and it takes the key's SHA-256 *digest* (never the secret)
and returns identity columns only.

Rule from the step, enforced structurally: the organization/application a
request runs as come from **this stored record**, never from a client header.
:class:`~app.services.applications.PostgresApplicationStore` then opens a
tenant session for the resolved organization, so every subsequent query is
RLS-scoped exactly like a human request.

Week 3 (Day 15+) wires :class:`ApiKeyDirectory` into a gateway auth
dependency; today it is the tested resolution seam.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.keys import KeyState, derive_key_state, is_api_key_shape
from app.auth.repository import _coerce_uuid, _plain_scope  # reviewed Day 012 internals
from app.auth.security import token_digest


@dataclass(frozen=True, slots=True)
class ApiKeyIdentity:
    """What the definer lookup returns for one presented digest."""

    key_id: uuid.UUID
    organization_id: uuid.UUID
    application_id: uuid.UUID
    scopes: str
    expires_at: datetime | None
    revoked_at: datetime | None


class ApiKeyDirectory:
    """Resolves raw keys to identities as the least-privilege API role."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def lookup(self, raw_key: str) -> ApiKeyIdentity | None:
        """Return the stored identity for ``raw_key``, or None if unknown.

        The raw secret is hashed in-process; only the digest reaches SQL —
        never the key itself — so it cannot appear in query logs either.
        Malformed shapes fail closed without even paying the round trip.
        """
        if not is_api_key_shape(raw_key):
            return None
        async with _plain_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    sa.text(
                        "SELECT key_id, organization_id, application_id, scopes, "
                        "expires_at, revoked_at FROM app_lookup_api_key(:digest)"
                    ),
                    {"digest": token_digest(raw_key)},
                )
            ).first()
        if row is None:
            return None
        return ApiKeyIdentity(
            key_id=_coerce_uuid(row.key_id),
            organization_id=_coerce_uuid(row.organization_id),
            application_id=_coerce_uuid(row.application_id),
            scopes=str(row.scopes),
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
        )

    async def resolve_usable(self, raw_key: str, *, now: datetime) -> ApiKeyIdentity | None:
        """Authenticate a machine credential: known, unrevoked, unexpired.

        Returns the identity only when the key is currently usable; a revoked
        or expired key resolves to ``None`` — indistinguishable from an
        unknown one at this boundary, so the API grants no oracle about which
        failure occurred.
        """
        identity = await self.lookup(raw_key)
        if identity is None:
            return None
        state = derive_key_state(
            revoked_at=identity.revoked_at, expires_at=identity.expires_at, now=now
        )
        return identity if state is KeyState.ACTIVE else None


__all__ = ["ApiKeyDirectory", "ApiKeyIdentity"]
