"""Application & scoped-API-key store with transactional audit (Day 013).

The storage port the applications/keys endpoints depend on, declared as a
:class:`typing.Protocol` with a PostgreSQL implementation — the same shape
Day 012 proved to be testable (contract tests inject an in-memory fake via
``app.state``, security tests run this class as the real ``sentinel_app``
role against live PostgreSQL).

Invariants enforced here, not at the edge:

* **Tenant comes from the principal.** Every method takes the authenticated
  ``organization_id`` and runs inside :func:`tenant_session_scope`, so RLS is
  the backstop even if a filter were forgotten; a foreign row is simply
  invisible (reads) or rejected by ``WITH CHECK`` (writes).
* **Audit rides the mutation's transaction.** ``append_audit`` writes into
  the *same* session as the protected change — commit together or vanish
  together.
* **Cursor pagination is keyset, not offset** — stable under concurrent
  inserts, opaque to clients, and bounded by the API layer's ``limit``.
* **Raw keys exist once.** Only :meth:`create_api_key` and
  :meth:`rotate_api_key` ever return a raw secret, inside
  :class:`IssuedApiKey`; every other read yields :class:`ApiKeyView`, which
  has no hash field to leak.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

import sqlalchemy as sa
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.keys import (
    GeneratedKey,
    KeyState,
    canonicalize_scopes,
    derive_key_state,
    generate_api_key,
    parse_scopes,
    rotation_overlap_expiry,
)
from app.core.errors import AppError
from app.core.time import utcnow
from app.infrastructure.database import tenant_session_scope
from app.models.api_key import ApiKey
from app.models.application import Application, ApplicationStatus
from app.models.audit import AuditActorType, AuditLog
from app.services.audit import AuditAction, AuditResource, append_audit

#: Hard ceiling on page size even if the edge passes something larger.
MAX_PAGE_LIMIT = 100


class NotFoundError(AppError):
    """Resource absent *or invisible to this tenant* — deliberately identical."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class NameConflictError(AppError):
    """The organization already has an application with that name."""

    status_code = status.HTTP_409_CONFLICT
    code = "name_conflict"


class ApplicationNotActiveError(AppError):
    """Archived applications cannot receive new credentials."""

    status_code = status.HTTP_409_CONFLICT
    code = "application_not_active"


class KeyNotRotatableError(AppError):
    """Revoked keys are dead evidence; rotation only applies to live ones."""

    status_code = status.HTTP_409_CONFLICT
    code = "key_not_rotatable"


class InvalidCursorError(AppError):
    """A pagination cursor was malformed — a client bug, not a retry."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "invalid_cursor"


class ConstraintViolationError(AppError):
    """Fallback for constraint failures that are not the org-unique clash.

    The message stays generic: constraint/SQL text never crosses the edge.
    """

    status_code = status.HTTP_409_CONFLICT
    code = "constraint_violation"


# ---------------------------------------------------------------------------
# Read models (never carry secrets or hashes)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApplicationView:
    id: uuid.UUID
    organization_id: uuid.UUID
    owner_user_id: uuid.UUID
    name: str
    description: str | None
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ApiKeyView:
    """Listable key metadata. There is no field that *could* hold a secret."""

    id: uuid.UUID
    application_id: uuid.UUID
    name: str
    key_prefix: str
    scopes: frozenset[str]
    state: KeyState
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    superseded_by_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class IssuedApiKey:
    """The single response type allowed to carry a raw key — show-once."""

    view: ApiKeyView
    secret: str


@dataclass(frozen=True, slots=True)
class AuditView:
    id: uuid.UUID
    actor_type: str
    actor_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: uuid.UUID
    detail: dict[str, object] | None
    request_id: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: list[T] = field(default_factory=list)
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Opaque keyset cursors: "<created_at iso>|<uuid>", base64url
# ---------------------------------------------------------------------------


def encode_cursor(created_at: datetime, last_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{last_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        timestamp_text, id_text = decoded.rsplit("|", 1)
        moment = datetime.fromisoformat(timestamp_text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment, uuid.UUID(id_text)
    except (ValueError, UnicodeError, sa.exc.SQLAlchemyError) as exc:
        raise InvalidCursorError("Pagination cursor is malformed.") from exc


# ---------------------------------------------------------------------------
# Storage port
# ---------------------------------------------------------------------------


class ApplicationStore(Protocol):
    """Everything the Day 013 endpoints need; nothing more."""

    async def create_application(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        name: str,
        description: str | None,
        request_id: str | None,
    ) -> ApplicationView: ...

    async def get_application(
        self, *, organization_id: uuid.UUID, application_id: uuid.UUID
    ) -> ApplicationView: ...

    async def list_applications(
        self, *, organization_id: uuid.UUID, cursor: str | None, limit: int
    ) -> Page[ApplicationView]: ...

    async def update_application(
        self,
        *,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
        request_id: str | None,
    ) -> ApplicationView: ...

    async def create_api_key(
        self,
        *,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        name: str,
        scopes: Sequence[str],
        expires_in_days: int | None,
        request_id: str | None,
    ) -> IssuedApiKey: ...

    async def list_api_keys(
        self,
        *,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        cursor: str | None,
        limit: int,
        now: datetime | None = None,
    ) -> Page[ApiKeyView]: ...

    async def revoke_api_key(
        self,
        *,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        key_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        request_id: str | None,
        now: datetime | None = None,
    ) -> ApiKeyView: ...

    async def rotate_api_key(
        self,
        *,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        key_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        grace_seconds: int,
        request_id: str | None,
        now: datetime | None = None,
    ) -> tuple[IssuedApiKey, ApiKeyView]: ...

    async def list_audit_logs(
        self,
        *,
        organization_id: uuid.UUID,
        cursor: str | None,
        limit: int,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
    ) -> Page[AuditView]: ...


# ---------------------------------------------------------------------------
# Row -> view mapping
# ---------------------------------------------------------------------------


def _application_view(row: Application) -> ApplicationView:
    return ApplicationView(
        id=row.id,
        organization_id=row.organization_id,
        owner_user_id=row.owner_user_id,
        name=row.name,
        description=row.description,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _key_view(row: ApiKey, now: datetime) -> ApiKeyView:
    return ApiKeyView(
        id=row.id,
        application_id=row.application_id,
        name=row.name,
        key_prefix=row.key_prefix,
        scopes=parse_scopes(row.scopes),
        state=derive_key_state(revoked_at=row.revoked_at, expires_at=row.expires_at, now=now),
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        superseded_by_id=row.superseded_by_id,
    )


def _audit_view(row: AuditLog) -> AuditView:
    detail: dict[str, object] | None = json.loads(row.detail) if row.detail else None
    return AuditView(
        id=row.id,
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        detail=detail,
        request_id=row.request_id,
        created_at=row.created_at,
    )


def _clamped(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_LIMIT))


class PostgresApplicationStore:
    """:class:`ApplicationStore` backed by the runtime ``sentinel_app`` role."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # -- applications --------------------------------------------------------

    async def create_application(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        name: str,
        description: str | None,
        request_id: str | None,
    ) -> ApplicationView:
        try:
            async with tenant_session_scope(self._session_factory, organization_id) as session:
                row = Application(
                    organization_id=organization_id,
                    owner_user_id=actor_user_id,
                    name=name,
                    description=description,
                    status=ApplicationStatus.ACTIVE.value,
                )
                session.add(row)
                await session.flush()  # populate row.id for the audit resource link
                append_audit(
                    session,
                    organization_id=organization_id,
                    actor_type=AuditActorType.USER,
                    actor_id=actor_user_id,
                    action=AuditAction.APPLICATION_CREATED,
                    resource_type=AuditResource.APPLICATION,
                    resource_id=row.id,
                    detail={"name": name, "status": row.status},
                    request_id=request_id,
                )
                return _application_view(row)
        except sa.exc.IntegrityError as exc:
            raise self._map_integrity_error(exc, org_name=f"application '{name}'") from exc

    async def get_application(
        self, *, organization_id: uuid.UUID, application_id: uuid.UUID
    ) -> ApplicationView:
        async with tenant_session_scope(self._session_factory, organization_id) as session:
            row = await self._load_application(session, application_id)
            return _application_view(row)

    async def list_applications(
        self, *, organization_id: uuid.UUID, cursor: str | None, limit: int
    ) -> Page[ApplicationView]:
        page_size = _clamped(limit)
        async with tenant_session_scope(self._session_factory, organization_id) as session:
            stmt = sa.select(Application).order_by(
                Application.created_at.desc(), Application.id.desc()
            )
            if cursor is not None:
                moment, last_id = decode_cursor(cursor)
                stmt = stmt.where(
                    sa.tuple_(Application.created_at, Application.id) < (moment, last_id)
                )
            rows = list((await session.execute(stmt.limit(page_size + 1))).scalars())
        items = [_application_view(row) for row in rows[:page_size]]
        next_cursor = (
            encode_cursor(rows[page_size - 1].created_at, rows[page_size - 1].id)
            if len(rows) > page_size
            else None
        )
        return Page(items=items, next_cursor=next_cursor)

    async def update_application(
        self,
        *,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
        request_id: str | None,
    ) -> ApplicationView:
        changed: dict[str, object] = {}
        try:
            async with tenant_session_scope(self._session_factory, organization_id) as session:
                row = await self._load_application(session, application_id)
                if name is not None and name != row.name:
                    row.name = name
                    changed["name"] = name
                if description is not None and description != row.description:
                    row.description = description
                    changed["description"] = description
                if status is not None and status != row.status:
                    row.status = status
                    changed["status"] = status
                if changed:
                    row.updated_at = utcnow()
                    append_audit(
                        session,
                        organization_id=organization_id,
                        actor_type=AuditActorType.USER,
                        actor_id=actor_user_id,
                        action=AuditAction.APPLICATION_UPDATED,
                        resource_type=AuditResource.APPLICATION,
                        resource_id=row.id,
                        detail=dict(changed),
                        request_id=request_id,
                    )
                return _application_view(row)
        except sa.exc.IntegrityError as exc:
            raise self._map_integrity_error(exc, org_name=f"application '{name}'") from exc

    # -- API keys ------------------------------------------------------------

    async def create_api_key(
        self,
        *,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        name: str,
        scopes: Sequence[str],
        expires_in_days: int | None,
        request_id: str | None,
    ) -> IssuedApiKey:
        generated: GeneratedKey = generate_api_key()
        canonical = canonicalize_scopes(scopes)
        now = utcnow()
        expires_at = now + timedelta(days=expires_in_days) if expires_in_days is not None else None
        async with tenant_session_scope(self._session_factory, organization_id) as session:
            application = await self._load_application(session, application_id)
            if application.status != ApplicationStatus.ACTIVE.value:
                raise ApplicationNotActiveError("Only active applications can receive API keys.")
            row = ApiKey(
                organization_id=organization_id,
                application_id=application_id,
                created_by_user_id=actor_user_id,
                name=name,
                key_prefix=generated.key_prefix,
                secret_hash=generated.secret_hash,
                scopes=canonical,
                expires_at=expires_at,
            )
            session.add(row)
            await session.flush()  # fail loudly *inside* the tx if a digest somehow collides
            # NOTE: the audit detail carries the key's id/name/scopes — never
            # the raw secret, and not even the display prefix (the database
            # CHECK rejects any 'sk_live_' marker in detail by design).
            append_audit(
                session,
                organization_id=organization_id,
                actor_type=AuditActorType.USER,
                actor_id=actor_user_id,
                action=AuditAction.API_KEY_CREATED,
                resource_type=AuditResource.API_KEY,
                resource_id=row.id,
                detail={
                    "application_id": str(application_id),
                    "name": name,
                    "scopes": sorted(parse_scopes(canonical)),
                    "expires_at": expires_at.isoformat() if expires_at else None,
                },
                request_id=request_id,
            )
            return IssuedApiKey(view=_key_view(row, now), secret=generated.raw)

    async def list_api_keys(
        self,
        *,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        cursor: str | None,
        limit: int,
        now: datetime | None = None,
    ) -> Page[ApiKeyView]:
        moment = now or utcnow()
        page_size = _clamped(limit)
        async with tenant_session_scope(self._session_factory, organization_id) as session:
            await self._load_application(session, application_id)
            stmt = (
                sa.select(ApiKey)
                .where(ApiKey.application_id == application_id)
                .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
            )
            if cursor is not None:
                c_ts, c_id = decode_cursor(cursor)
                stmt = stmt.where(sa.tuple_(ApiKey.created_at, ApiKey.id) < (c_ts, c_id))
            rows = list((await session.execute(stmt.limit(page_size + 1))).scalars())
        items = [_key_view(row, moment) for row in rows[:page_size]]
        next_cursor = (
            encode_cursor(rows[page_size - 1].created_at, rows[page_size - 1].id)
            if len(rows) > page_size
            else None
        )
        return Page(items=items, next_cursor=next_cursor)

    async def revoke_api_key(
        self,
        *,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        key_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        request_id: str | None,
        now: datetime | None = None,
    ) -> ApiKeyView:
        moment = now or utcnow()
        async with tenant_session_scope(self._session_factory, organization_id) as session:
            row = await self._load_key(session, application_id, key_id)
            if row.revoked_at is None:
                # Immediate: one UPDATE, no grace, and the row stays (the API
                # role has no DELETE on api_keys — revocation evidence survives).
                row.revoked_at = moment
                row.updated_at = moment
                append_audit(
                    session,
                    organization_id=organization_id,
                    actor_type=AuditActorType.USER,
                    actor_id=actor_user_id,
                    action=AuditAction.API_KEY_REVOKED,
                    resource_type=AuditResource.API_KEY,
                    resource_id=row.id,
                    detail={"application_id": str(application_id), "name": row.name},
                    request_id=request_id,
                )
            return _key_view(row, moment)

    async def rotate_api_key(
        self,
        *,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        key_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        grace_seconds: int,
        request_id: str | None,
        now: datetime | None = None,
    ) -> tuple[IssuedApiKey, ApiKeyView]:
        moment = now or utcnow()
        generated: GeneratedKey = generate_api_key()
        async with tenant_session_scope(self._session_factory, organization_id) as session:
            await self._load_application(session, application_id)
            predecessor = await self._load_key(session, application_id, key_id)
            if predecessor.revoked_at is not None:
                raise KeyNotRotatableError(
                    "Revoked keys cannot be rotated; issue a new key instead."
                )
            successor = ApiKey(
                organization_id=organization_id,
                application_id=application_id,
                created_by_user_id=actor_user_id,
                name=predecessor.name,
                key_prefix=generated.key_prefix,
                secret_hash=generated.secret_hash,
                scopes=predecessor.scopes,
            )
            session.add(successor)
            await session.flush()  # successor.id needed for the lineage pointer
            # Deliberate overlap: the old key keeps working until
            # min(its expiry, now + grace), capped — grace never extends life.
            predecessor.superseded_by_id = successor.id
            predecessor.expires_at = rotation_overlap_expiry(
                previous_expires_at=predecessor.expires_at,
                grace_seconds=grace_seconds,
                now=moment,
            )
            predecessor.updated_at = moment
            append_audit(
                session,
                organization_id=organization_id,
                actor_type=AuditActorType.USER,
                actor_id=actor_user_id,
                action=AuditAction.API_KEY_ROTATED,
                resource_type=AuditResource.API_KEY,
                resource_id=predecessor.id,
                detail={
                    "application_id": str(application_id),
                    "successor_key_id": str(successor.id),
                    "overlap_expires_at": (
                        predecessor.expires_at.isoformat() if predecessor.expires_at else None
                    ),
                },
                request_id=request_id,
            )
            return (
                IssuedApiKey(view=_key_view(successor, moment), secret=generated.raw),
                _key_view(predecessor, moment),
            )

    # -- audit trail (read side) ----------------------------------------------

    async def list_audit_logs(
        self,
        *,
        organization_id: uuid.UUID,
        cursor: str | None,
        limit: int,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
    ) -> Page[AuditView]:
        page_size = _clamped(limit)
        async with tenant_session_scope(self._session_factory, organization_id) as session:
            stmt = sa.select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            if resource_type is not None:
                stmt = stmt.where(AuditLog.resource_type == resource_type)
            if resource_id is not None:
                stmt = stmt.where(AuditLog.resource_id == resource_id)
            if cursor is not None:
                c_ts, c_id = decode_cursor(cursor)
                stmt = stmt.where(sa.tuple_(AuditLog.created_at, AuditLog.id) < (c_ts, c_id))
            rows = list((await session.execute(stmt.limit(page_size + 1))).scalars())
        items = [_audit_view(row) for row in rows[:page_size]]
        next_cursor = (
            encode_cursor(rows[page_size - 1].created_at, rows[page_size - 1].id)
            if len(rows) > page_size
            else None
        )
        return Page(items=items, next_cursor=next_cursor)

    # -- helpers ---------------------------------------------------------------

    async def _load_application(
        self, session: AsyncSession, application_id: uuid.UUID
    ) -> Application:
        row = (
            await session.execute(sa.select(Application).where(Application.id == application_id))
        ).scalar_one_or_none()
        if row is None:
            # Under RLS a foreign application and a never-existing one are the
            # same answer — no existence leakage across tenants.
            raise NotFoundError("Application not found.")
        return row

    async def _load_key(
        self, session: AsyncSession, application_id: uuid.UUID, key_id: uuid.UUID
    ) -> ApiKey:
        row = (
            await session.execute(
                sa.select(ApiKey).where(
                    ApiKey.id == key_id, ApiKey.application_id == application_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("API key not found.")
        return row

    @staticmethod
    def _map_integrity_error(exc: sa.exc.IntegrityError, *, org_name: str) -> AppError:
        # Classify without echoing SQL text (constraint names are stable):
        # uq_applications_org_name is the only UNIQUE an app write can hit.
        if "uq_applications_org_name" in str(exc.orig):
            return NameConflictError(f"An application named {org_name} already exists.")
        return ConstraintViolationError("The write violated a database constraint.")


__all__ = [
    "MAX_PAGE_LIMIT",
    "ApplicationNotActiveError",
    "ApplicationStore",
    "ApplicationView",
    "AuditView",
    "ApiKeyView",
    "ConstraintViolationError",
    "InvalidCursorError",
    "IssuedApiKey",
    "KeyNotRotatableError",
    "NameConflictError",
    "NotFoundError",
    "Page",
    "PostgresApplicationStore",
    "decode_cursor",
    "encode_cursor",
]
