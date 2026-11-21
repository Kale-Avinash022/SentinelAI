"""Immutable version creation, redacted diffing and CAS activation (Day 014).

The storage port behind ``/api/v1/configs``, declared as a
:class:`typing.Protocol` with a PostgreSQL implementation — the same shape
Days 012/013 proved testable (contract tests inject an in-memory fake,
security tests run this class as the real ``sentinel_app`` role against live
PostgreSQL).

The step's four acceptance checks are implemented as facts, not conventions:

* **"Historical versions cannot be edited in place."** There is no update
  method on this store at all, the model has no ``updated_at``, and revision
  ``0007`` grants ``sentinel_app`` only ``SELECT`` + ``INSERT`` on
  ``config_versions``. A new idea produces a new row with the next
  ``version_number``; the previous row is untouched forever.
* **"Activation uses an expected-version check and produces audit history."**
  :meth:`PostgresConfigVersionStore.activate` reads the slot's live activation,
  refuses a mismatch with :class:`ActivationConflictError`, closes the live row
  with ``UPDATE ... WHERE id = :live_id AND superseded_at IS NULL`` and demands
  ``rowcount == 1``, then appends the new activation and its audit row in the
  *same* transaction. Two genuine races are both caught: the predicate above
  (loser sees zero rows) and the partial unique index
  ``uq_config_activations_live_slot`` (two simultaneous first-activations).
* **"Diffs redact sensitive values and credentials remain external
  references."** :meth:`diff_versions` never reads ``encrypted_payload``: it
  compares stored metadata through :func:`~app.configs.rules.diff_payloads`
  (redact-by-name) and derives the body change from ``content_hash``
  differences, so a diff answers "what changed" without ever answering "what
  was it". Provider secrets are not in this table at all — ``secret_ref`` is a
  ``secret://`` locator policed by a CHECK.
* **"Last-known-good requires explicit qualification evidence."**
  :meth:`activate` refuses ``is_last_known_good=True`` without a
  ``qualification`` payload, the database CHECK repeats the rule, and
  :meth:`get_last_known_good` *derives* the answer from the newest qualifying
  activation rather than from a mutable "current" column — because there is no
  column to lie.

Reading a sealed body is a *privileged, audited* operation
(:meth:`reveal_body`): the ciphertext is opened through the injected
:class:`~app.configs.vault.PayloadVault` and every reveal appends
``config_version.body_revealed``, so "who read the prompt" is answerable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, cast

import sqlalchemy as sa
from fastapi import status
from pydantic import ValidationError
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.configs.rules import (
    ConfigKind,
    DiffEntry,
    PayloadSemanticsError,
    ProviderRoutePayload,
    canonical_json,
    content_hash,
    diff_body,
    diff_payloads,
    fingerprint,
    parse_payload,
    validate_slot_name,
    validate_version_pairing,
)
from app.configs.vault import PayloadVault, VaultError
from app.core.errors import AppError
from app.core.time import utcnow
from app.infrastructure.database import tenant_session_scope
from app.models.audit import AuditActorType
from app.models.config_version import ConfigActivation, ConfigVersion
from app.models.provider_catalog import ProviderCatalogEntry
from app.services.applications import (
    MAX_PAGE_LIMIT,
    NotFoundError,
    Page,
    decode_cursor,
    encode_cursor,
)
from app.services.audit import (
    FORBIDDEN_DETAIL_MARKERS,
    AuditAction,
    AuditResource,
    append_audit,
)

#: Where a version keeps its sensitive body. ``none`` = a provider route, which
#: has no body by schema. Never reveals the ciphertext or the reference value.
BodyStorage = Literal["encrypted", "reference", "none"]


class InvalidConfigPayloadError(AppError):
    """Metadata or body/reference pairing rejected before anything is written."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "invalid_config_payload"


class VersionConflictError(AppError):
    """Another writer claimed this ladder position first — read and retry."""

    status_code = status.HTTP_409_CONFLICT
    code = "version_conflict"
    retryable = True


class ActivationConflictError(AppError):
    """The slot is not in the state the writer expected; nothing was changed."""

    status_code = status.HTTP_409_CONFLICT
    code = "activation_conflict"
    retryable = True


class IncompatibleConfigurationError(AppError):
    """The version is well-formed but cannot be activated in this tenant."""

    status_code = status.HTTP_409_CONFLICT
    code = "incompatible_configuration"


class QualificationRequiredError(AppError):
    """Last-known-good was claimed without explicit qualification evidence."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "qualification_required"


class BodyNotAvailableError(AppError):
    """The body lives behind an external reference, so there is nothing to seal."""

    status_code = status.HTTP_409_CONFLICT
    code = "body_not_available"


class BodyUndecryptableError(AppError):
    """A sealed body could not be opened. The vault's reason stays internal."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "body_undecryptable"
    retryable = True


class ConfigConstraintViolationError(AppError):
    """Fallback for constraints that are not one of the classified clashes."""

    status_code = status.HTTP_409_CONFLICT
    code = "constraint_violation"


# ---------------------------------------------------------------------------
# Read models. None of them carries a ciphertext column or a prompt body, so a
# serialized response cannot leak one by accident.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfigVersionView:
    id: uuid.UUID
    organization_id: uuid.UUID
    kind: str
    name: str
    version_number: int
    content_hash: str
    payload: dict[str, Any]
    body_storage: BodyStorage
    secret_ref_fingerprint: str | None
    created_by_user_id: uuid.UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ActivationView:
    id: uuid.UUID
    organization_id: uuid.UUID
    kind: str
    name: str
    version_id: uuid.UUID
    version_number: int
    expected_previous_number: int | None
    supersedes_id: uuid.UUID | None
    activated_by_user_id: uuid.UUID
    activated_at: datetime
    superseded_at: datetime | None
    qualification: dict[str, Any] | None
    is_last_known_good: bool
    is_active: bool


@dataclass(frozen=True, slots=True)
class VersionDiffView:
    """A redacted comparison. ``entries`` values are never raw sensitive text."""

    kind: str
    name: str
    before_version_id: uuid.UUID
    after_version_id: uuid.UUID
    before_version_number: int
    after_version_number: int
    before_content_hash: str
    after_content_hash: str
    identical: bool
    entries: list[DiffEntry]
    #: True when the content hashes differ but some metadata/reference entry
    #: already explains part of it: the template may also have changed, but a
    #: hash over the whole version cannot attribute the remainder, and guessing
    #: would be worse than reporting the uncertainty.
    body_change_unverified: bool


@dataclass(frozen=True, slots=True)
class RevealedBody:
    """The only type that ever carries plaintext template text."""

    version_id: uuid.UUID
    version_number: int
    content_hash: str
    body: str


# ---------------------------------------------------------------------------
# Storage port
# ---------------------------------------------------------------------------


class ConfigVersionStore(Protocol):
    """Create, read, compare and activate immutable configuration versions."""

    async def create_version(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        kind: str,
        name: str,
        payload: Mapping[str, Any],
        body: str | None = None,
        secret_ref: str | None = None,
        request_id: str | None,
    ) -> ConfigVersionView: ...

    async def get_version(
        self, *, organization_id: uuid.UUID, version_id: uuid.UUID
    ) -> ConfigVersionView: ...

    async def list_versions(
        self,
        *,
        organization_id: uuid.UUID,
        kind: str,
        name: str,
        cursor: str | None,
        limit: int,
    ) -> Page[ConfigVersionView]: ...

    async def diff_versions(
        self,
        *,
        organization_id: uuid.UUID,
        before_version_id: uuid.UUID,
        after_version_id: uuid.UUID,
    ) -> VersionDiffView: ...

    async def reveal_body(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        version_id: uuid.UUID,
        request_id: str | None,
    ) -> RevealedBody: ...

    async def activate(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        kind: str,
        name: str,
        version_id: uuid.UUID,
        expected_previous_number: int | None,
        qualification: Mapping[str, Any] | None,
        is_last_known_good: bool,
        request_id: str | None,
    ) -> ActivationView: ...

    async def get_active(
        self, *, organization_id: uuid.UUID, kind: str, name: str
    ) -> ActivationView: ...

    async def get_last_known_good(
        self, *, organization_id: uuid.UUID, kind: str, name: str
    ) -> ActivationView: ...

    async def list_activations(
        self,
        *,
        organization_id: uuid.UUID,
        kind: str,
        name: str,
        cursor: str | None,
        limit: int,
    ) -> Page[ActivationView]: ...


# ---------------------------------------------------------------------------
# Row -> view mapping
# ---------------------------------------------------------------------------


def _body_storage_of(row: ConfigVersion) -> BodyStorage:
    if row.encrypted_payload is not None:
        return "encrypted"
    if row.secret_ref is not None:
        return "reference"
    return "none"


def _version_view(row: ConfigVersion) -> ConfigVersionView:
    return ConfigVersionView(
        id=row.id,
        organization_id=row.organization_id,
        kind=row.kind,
        name=row.name,
        version_number=row.version_number,
        content_hash=row.content_hash,
        payload=json.loads(row.payload),
        body_storage=_body_storage_of(row),
        secret_ref_fingerprint=fingerprint(row.secret_ref) if row.secret_ref else None,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
    )


def _activation_view(row: ConfigActivation) -> ActivationView:
    return ActivationView(
        id=row.id,
        organization_id=row.organization_id,
        kind=row.kind,
        name=row.name,
        version_id=row.version_id,
        version_number=row.version_number,
        expected_previous_number=row.expected_previous_number,
        supersedes_id=row.supersedes_id,
        activated_by_user_id=row.activated_by_user_id,
        activated_at=row.activated_at,
        superseded_at=row.superseded_at,
        qualification=json.loads(row.qualification) if row.qualification else None,
        is_last_known_good=row.is_last_known_good,
        is_active=row.superseded_at is None,
    )


def _clamped(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_LIMIT))


def _typed_kind(kind: str) -> ConfigKind:
    try:
        return ConfigKind(kind)
    except ValueError as exc:
        # Bounded echo: ``kind`` is client text, and an unbounded value must not
        # be copied into a response body.
        raise InvalidConfigPayloadError(
            f"Unknown configuration kind {kind[:24]!r}; expected one of: "
            + ", ".join(sorted(value.value for value in ConfigKind))
            + "."
        ) from exc


def _reject_secret_material(value: object, *, field: str) -> None:
    """The same marker rule the audit trail enforces, applied to qualifications."""
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_secret_material(item, field=field)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _reject_secret_material(item, field=field)
    elif isinstance(value, str) and any(marker in value for marker in FORBIDDEN_DETAIL_MARKERS):
        raise InvalidConfigPayloadError(f"{field} must not contain secret or sealed-payload text.")


class PostgresConfigVersionStore:
    """:class:`ConfigVersionStore` backed by the runtime ``sentinel_app`` role."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], vault: PayloadVault
    ) -> None:
        self._session_factory = session_factory
        self._vault = vault

    # -- versions ------------------------------------------------------------

    async def create_version(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        kind: str,
        name: str,
        payload: Mapping[str, Any],
        body: str | None = None,
        secret_ref: str | None = None,
        request_id: str | None,
    ) -> ConfigVersionView:
        """Validate, content-address and append the next version in a ladder.

        All validation happens before the first statement so a rejected
        proposal costs no rows; the ladder position is assigned here (never
        supplied by a client) and protected by
        ``uq_config_versions_org_kind_name_number``.
        """
        typed_kind = _typed_kind(kind)
        try:
            slot = validate_slot_name(name)
        except PayloadSemanticsError as exc:
            raise InvalidConfigPayloadError(str(exc)) from exc
        try:
            parsed = parse_payload(typed_kind, payload)
        except ValidationError as exc:
            # Field pointers only — submitted values may themselves be secret.
            raise InvalidConfigPayloadError(
                "Configuration payload failed schema validation: "
                + "; ".join(
                    f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                    for error in exc.errors()
                )
            ) from exc
        try:
            validate_version_pairing(typed_kind, parsed, body=body, secret_ref=secret_ref)
        except PayloadSemanticsError as exc:
            raise InvalidConfigPayloadError(str(exc)) from exc

        metadata = parsed.model_dump(mode="json")
        canonical = canonical_json(metadata)
        digest = content_hash(typed_kind.value, metadata, body=body, secret_ref=secret_ref)
        sealed = self._vault.seal(body) if body is not None and body.strip() != "" else None

        try:
            async with tenant_session_scope(self._session_factory, organization_id) as session:
                row = ConfigVersion(
                    organization_id=organization_id,
                    kind=typed_kind.value,
                    name=slot,
                    version_number=await self._next_version_number(session, typed_kind, slot),
                    content_hash=digest,
                    payload=canonical,
                    secret_ref=secret_ref.strip() if secret_ref else None,
                    encrypted_payload=sealed,
                    created_by_user_id=actor_user_id,
                )
                session.add(row)
                await session.flush()  # fail loudly inside the tx on a ladder clash
                append_audit(
                    session,
                    organization_id=organization_id,
                    actor_type=AuditActorType.USER,
                    actor_id=actor_user_id,
                    action=AuditAction.CONFIG_VERSION_CREATED,
                    resource_type=AuditResource.CONFIG_VERSION,
                    resource_id=row.id,
                    detail={
                        "kind": row.kind,
                        "name": row.name,
                        "version_number": row.version_number,
                        "content_hash": row.content_hash,
                        "body_storage": _body_storage_of(row),
                    },
                    request_id=request_id,
                )
                return _version_view(row)
        except sa.exc.IntegrityError as exc:
            raise self._map_integrity_error(exc) from exc

    async def get_version(
        self, *, organization_id: uuid.UUID, version_id: uuid.UUID
    ) -> ConfigVersionView:
        async with tenant_session_scope(self._session_factory, organization_id) as session:
            return _version_view(await self._load_version(session, version_id))

    async def list_versions(
        self,
        *,
        organization_id: uuid.UUID,
        kind: str,
        name: str,
        cursor: str | None,
        limit: int,
    ) -> Page[ConfigVersionView]:
        typed_kind = _typed_kind(kind)
        slot = name.strip()
        page_size = _clamped(limit)
        async with tenant_session_scope(self._session_factory, organization_id) as session:
            stmt = (
                sa.select(ConfigVersion)
                .where(
                    ConfigVersion.kind == typed_kind.value,
                    ConfigVersion.name == slot,
                )
                .order_by(ConfigVersion.version_number.desc(), ConfigVersion.id.desc())
            )
            if cursor is not None:
                moment, last_id = decode_cursor(cursor)
                stmt = stmt.where(
                    sa.tuple_(ConfigVersion.created_at, ConfigVersion.id) < (moment, last_id)
                )
            rows = list((await session.execute(stmt.limit(page_size + 1))).scalars())
        items = [_version_view(row) for row in rows[:page_size]]
        next_cursor = (
            encode_cursor(rows[page_size - 1].created_at, rows[page_size - 1].id)
            if len(rows) > page_size
            else None
        )
        return Page(items=items, next_cursor=next_cursor)

    async def diff_versions(
        self,
        *,
        organization_id: uuid.UUID,
        before_version_id: uuid.UUID,
        after_version_id: uuid.UUID,
    ) -> VersionDiffView:
        """Redacted field-level comparison of two versions in the same ladder.

        The ciphertext column is never selected into this path, and the body
        change is *derived*: if the metadata and the reference are identical but
        the content hashes differ, the only remaining input to the hash is the
        template, so the diff can prove "the body changed" and commit to each
        side with a fingerprint without ever decrypting anything.
        """
        async with tenant_session_scope(self._session_factory, organization_id) as session:
            before = await self._load_version(session, before_version_id)
            after = await self._load_version(session, after_version_id)
            if (before.kind, before.name) != (after.kind, after.name):
                raise InvalidConfigPayloadError(
                    "Versions to compare must belong to the same (kind, name) ladder."
                )
            entries = diff_payloads(json.loads(before.payload), json.loads(after.payload))
            before_ref, after_ref = before.secret_ref or "", after.secret_ref or ""
            if before_ref != after_ref:
                change: Literal["added", "removed", "changed"]
                if not before_ref:
                    change = "added"
                elif not after_ref:
                    change = "removed"
                else:
                    change = "changed"
                entries.append(
                    DiffEntry(
                        path="secret_ref",
                        change=change,
                        before=fingerprint(before_ref) if before_ref else None,
                        after=fingerprint(after_ref) if after_ref else None,
                    )
                )
            hashes_differ = before.content_hash != after.content_hash
            body_entry = (
                diff_body(before.content_hash, after.content_hash) if hashes_differ else None
            )
            unverified = False
            if body_entry is not None:
                if entries:
                    # Something else already explains the hash movement, so the
                    # template *may* also have changed — report that honestly
                    # instead of inventing a body entry we cannot attribute.
                    body_entry = None
                    unverified = True
                else:
                    entries.append(body_entry)
            return VersionDiffView(
                kind=before.kind,
                name=before.name,
                before_version_id=before.id,
                after_version_id=after.id,
                before_version_number=before.version_number,
                after_version_number=after.version_number,
                before_content_hash=before.content_hash,
                after_content_hash=after.content_hash,
                identical=not hashes_differ,
                entries=sorted(entries, key=lambda entry: entry.path),
                body_change_unverified=unverified,
            )

    async def reveal_body(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        version_id: uuid.UUID,
        request_id: str | None,
    ) -> RevealedBody:
        """Open a sealed template, recording the read in the trail.

        Access is audited *in the same transaction* as the (read-only) select,
        so a reveal that happened is a reveal that is recorded — the point of
        the step's "envelope encryption **and access audit** if stored".
        """
        async with tenant_session_scope(self._session_factory, organization_id) as session:
            row = await self._load_version(session, version_id)
            if row.encrypted_payload is None:
                raise BodyNotAvailableError(
                    "This version's body is stored externally by reference; "
                    "there is no sealed payload to reveal."
                )
            try:
                body = self._vault.open(row.encrypted_payload)
            except VaultError as exc:
                raise BodyUndecryptableError(
                    "The stored body could not be decrypted with the current key."
                ) from exc
            append_audit(
                session,
                organization_id=organization_id,
                actor_type=AuditActorType.USER,
                actor_id=actor_user_id,
                action=AuditAction.CONFIG_VERSION_BODY_REVEALED,
                resource_type=AuditResource.CONFIG_VERSION,
                resource_id=row.id,
                detail={
                    "kind": row.kind,
                    "name": row.name,
                    "version_number": row.version_number,
                    "content_hash": row.content_hash,
                },
                request_id=request_id,
            )
            return RevealedBody(
                version_id=row.id,
                version_number=row.version_number,
                content_hash=row.content_hash,
                body=body,
            )

    # -- activation (compare-and-swap) ---------------------------------------

    async def activate(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        kind: str,
        name: str,
        version_id: uuid.UUID,
        expected_previous_number: int | None,
        qualification: Mapping[str, Any] | None,
        is_last_known_good: bool,
        request_id: str | None,
    ) -> ActivationView:
        """Append the activation that makes ``version_id`` the slot's live one.

        Every failure mode leaves the database exactly as it was: the
        compatibility gate, the expected-version check, the zero-row close and
        the partial unique index all abort the single transaction.
        """
        typed_kind = _typed_kind(kind)
        slot = name.strip()
        if is_last_known_good and not qualification:
            raise QualificationRequiredError(
                "A version may only be marked last-known-good with explicit "
                "qualification evidence (evaluation run, scores, approver note)."
            )
        if qualification is not None:
            _reject_secret_material(qualification, field="qualification")

        moment = utcnow()
        try:
            async with tenant_session_scope(self._session_factory, organization_id) as session:
                version = await self._load_version(session, version_id)
                if (version.kind, version.name) != (typed_kind.value, slot):
                    raise IncompatibleConfigurationError(
                        f"Version {version_id} belongs to "
                        f"({version.kind}, {version.name}), not ({typed_kind.value}, {slot})."
                    )
                metadata = json.loads(version.payload)
                await self._require_compatible(session, typed_kind, metadata)

                live = (
                    await session.execute(
                        sa.select(ConfigActivation)
                        .where(
                            ConfigActivation.kind == typed_kind.value,
                            ConfigActivation.name == slot,
                            ConfigActivation.superseded_at.is_(None),
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()

                if live is None:
                    if expected_previous_number not in (None, 0):
                        raise ActivationConflictError(
                            f"Nothing is active in ({typed_kind.value}, {slot}), but the "
                            f"request expected version {expected_previous_number}."
                        )
                    supersedes_id = None
                else:
                    if (
                        expected_previous_number is not None
                        and expected_previous_number != live.version_number
                    ):
                        raise ActivationConflictError(
                            f"Expected previous version {expected_previous_number}, but "
                            f"version {live.version_number} is active."
                        )
                    # The CAS write itself: closing the *specific* row we checked,
                    # and only if it is still open. A concurrent activation that
                    # closed it first makes this match zero rows.
                    close = cast(
                        CursorResult[Any],
                        await session.execute(
                            sa.update(ConfigActivation)
                            .where(
                                ConfigActivation.id == live.id,
                                ConfigActivation.superseded_at.is_(None),
                            )
                            .values(superseded_at=moment)
                        ),
                    )
                    if close.rowcount != 1:
                        raise ActivationConflictError(
                            "Another activation changed this slot first; no write was made."
                        )
                    supersedes_id = live.id

                activation = ConfigActivation(
                    organization_id=organization_id,
                    kind=typed_kind.value,
                    name=slot,
                    version_id=version.id,
                    version_number=version.version_number,
                    expected_previous_number=(live.version_number if live is not None else None),
                    supersedes_id=supersedes_id,
                    activated_by_user_id=actor_user_id,
                    activated_at=moment,
                    qualification=canonical_json(dict(qualification)) if qualification else None,
                    is_last_known_good=is_last_known_good,
                )
                session.add(activation)
                await session.flush()
                append_audit(
                    session,
                    organization_id=organization_id,
                    actor_type=AuditActorType.USER,
                    actor_id=actor_user_id,
                    action=AuditAction.CONFIG_ACTIVATION_CREATED,
                    resource_type=AuditResource.CONFIG_ACTIVATION,
                    resource_id=activation.id,
                    detail={
                        "kind": activation.kind,
                        "name": activation.name,
                        "version_id": str(activation.version_id),
                        "version_number": activation.version_number,
                        "expected_previous_number": activation.expected_previous_number,
                        "supersedes_id": str(supersedes_id) if supersedes_id else None,
                        "is_last_known_good": activation.is_last_known_good,
                    },
                    request_id=request_id,
                )
                return _activation_view(activation)
        except sa.exc.IntegrityError as exc:
            raise self._map_integrity_error(exc) from exc

    async def get_active(
        self, *, organization_id: uuid.UUID, kind: str, name: str
    ) -> ActivationView:
        typed_kind = _typed_kind(kind)
        slot = name.strip()
        async with tenant_session_scope(self._session_factory, organization_id) as session:
            row = (
                await session.execute(
                    sa.select(ConfigActivation).where(
                        ConfigActivation.kind == typed_kind.value,
                        ConfigActivation.name == slot,
                        ConfigActivation.superseded_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError("No version is active for this configuration slot.")
            return _activation_view(row)

    async def get_last_known_good(
        self, *, organization_id: uuid.UUID, kind: str, name: str
    ) -> ActivationView:
        """Newest activation that *earned* the label by carrying qualification.

        Day 055's rollback target is derived here instead of stored in a mutable
        column, so no code path can promote a version by editing state.
        """
        typed_kind = _typed_kind(kind)
        slot = name.strip()
        async with tenant_session_scope(self._session_factory, organization_id) as session:
            row = (
                await session.execute(
                    sa.select(ConfigActivation)
                    .where(
                        ConfigActivation.kind == typed_kind.value,
                        ConfigActivation.name == slot,
                        ConfigActivation.is_last_known_good.is_(True),
                    )
                    .order_by(ConfigActivation.activated_at.desc(), ConfigActivation.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError("No last-known-good version qualifies for this slot.")
            return _activation_view(row)

    async def list_activations(
        self,
        *,
        organization_id: uuid.UUID,
        kind: str,
        name: str,
        cursor: str | None,
        limit: int,
    ) -> Page[ActivationView]:
        typed_kind = _typed_kind(kind)
        slot = name.strip()
        page_size = _clamped(limit)
        async with tenant_session_scope(self._session_factory, organization_id) as session:
            stmt = (
                sa.select(ConfigActivation)
                .where(
                    ConfigActivation.kind == typed_kind.value,
                    ConfigActivation.name == slot,
                )
                .order_by(ConfigActivation.activated_at.desc(), ConfigActivation.id.desc())
            )
            if cursor is not None:
                moment, last_id = decode_cursor(cursor)
                stmt = stmt.where(
                    sa.tuple_(ConfigActivation.activated_at, ConfigActivation.id)
                    < (moment, last_id)
                )
            rows = list((await session.execute(stmt.limit(page_size + 1))).scalars())
        items = [_activation_view(row) for row in rows[:page_size]]
        next_cursor = (
            encode_cursor(rows[page_size - 1].activated_at, rows[page_size - 1].id)
            if len(rows) > page_size
            else None
        )
        return Page(items=items, next_cursor=next_cursor)

    # -- helpers --------------------------------------------------------------

    async def _next_version_number(self, session: AsyncSession, kind: ConfigKind, name: str) -> int:
        current = (
            await session.execute(
                sa.select(sa.func.max(ConfigVersion.version_number)).where(
                    ConfigVersion.kind == kind.value, ConfigVersion.name == name
                )
            )
        ).scalar_one_or_none()
        return int(current) + 1 if current is not None else 1

    async def _load_version(self, session: AsyncSession, version_id: uuid.UUID) -> ConfigVersion:
        row = (
            await session.execute(sa.select(ConfigVersion).where(ConfigVersion.id == version_id))
        ).scalar_one_or_none()
        if row is None:
            # Under RLS a foreign version and a never-existing one are the same
            # answer — no existence leakage across tenants.
            raise NotFoundError("Configuration version not found.")
        return row

    async def _require_compatible(
        self,
        session: AsyncSession,
        kind: ConfigKind,
        metadata: Mapping[str, Any],
    ) -> None:
        """Refuse activation of a version that cannot actually serve traffic.

        The gates are about *references this table cannot enforce with a foreign
        key* because they live inside JSON (a prompt version's own metadata has
        no cross-table reference, so only routes are checked):

        * every allowed provider must exist in the global catalog, and the
          primary must support what the version promises (streaming);
        * a ``prompt_version_id`` must resolve to a prompt version *inside this
          tenant* — RLS scopes the select, so a foreign or deleted prompt is a
          conflict rather than a route to someone else's template.
        """
        if kind is ConfigKind.PROVIDER_ROUTE:
            route = ProviderRoutePayload.model_validate(metadata)
            unknown = await self._unknown_providers(session, route.allowed_providers)
            if unknown:
                raise IncompatibleConfigurationError(
                    "Providers are not in the global catalog: " + ", ".join(sorted(unknown)) + "."
                )
            if route.capabilities.streaming:
                primary = (
                    await session.execute(
                        sa.select(ProviderCatalogEntry.supports_streaming).where(
                            ProviderCatalogEntry.provider == route.primary_provider
                        )
                    )
                ).scalar_one_or_none()
                if not primary:
                    raise IncompatibleConfigurationError(
                        f"Provider {route.primary_provider} does not support streaming, "
                        "which this version's capabilities require."
                    )
            if route.prompt_version_id is not None:
                prompt_id = uuid.UUID(route.prompt_version_id)
                referenced = (
                    await session.execute(
                        sa.select(ConfigVersion.kind).where(ConfigVersion.id == prompt_id)
                    )
                ).scalar_one_or_none()
                if referenced is None:
                    raise IncompatibleConfigurationError(
                        f"Referenced prompt version {prompt_id} does not exist in this "
                        "organization."
                    )
                if referenced != ConfigKind.PROMPT.value:
                    raise IncompatibleConfigurationError(
                        f"Referenced version {prompt_id} is a {referenced} version, not a prompt."
                    )

    async def _unknown_providers(self, session: AsyncSession, providers: Sequence[str]) -> set[str]:
        rows = (
            await session.execute(
                sa.select(ProviderCatalogEntry.provider).where(
                    ProviderCatalogEntry.provider.in_(list(providers))
                )
            )
        ).scalars()
        return set(providers) - set(rows)

    @staticmethod
    def _map_integrity_error(exc: sa.exc.IntegrityError) -> AppError:
        # Classify by constraint name without echoing SQL text.
        message = str(exc.orig)
        if "uq_config_activations_live_slot" in message:
            return ActivationConflictError(
                "Another activation of this slot committed first; no write was made."
            )
        if "uq_config_versions_org_kind_name_number" in message:
            return VersionConflictError(
                "Another version claimed this ladder position first; read the history and retry."
            )
        if "ck_config_activations_lkg_requires_evidence" in message:
            return QualificationRequiredError(
                "Last-known-good requires explicit qualification evidence."
            )
        return ConfigConstraintViolationError("The write violated a database constraint.")


__all__ = [
    "ActivationConflictError",
    "ActivationView",
    "BodyNotAvailableError",
    "BodyStorage",
    "BodyUndecryptableError",
    "ConfigConstraintViolationError",
    "ConfigVersionStore",
    "ConfigVersionView",
    "IncompatibleConfigurationError",
    "InvalidConfigPayloadError",
    "NotFoundError",
    "Page",
    "PostgresConfigVersionStore",
    "QualificationRequiredError",
    "RevealedBody",
    "VersionConflictError",
    "VersionDiffView",
]
