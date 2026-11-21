"""Immutable config/prompt version endpoints (Day 014).

The HTTP surface over :mod:`app.services.config_versions`. Two things are
deliberate about its shape:

* **Reads and writes are asymmetric on purpose.** Metadata versions can be read
  by anyone with ``read:requests``, but a version is created only with
  ``administer:provider-config`` and activated only with ``approve:config``
  (Owner/Admin — ADR-0004). A *sensitive body* is readable by nobody by
  default: it has no field in any list/get response, and the dedicated
  ``reveal-body`` route is both permission-gated and audit-recorded, so "who
  read this prompt" is answerable from the trail.
* **No route can rewrite history.** There is no PUT/PATCH/DELETE here at all —
  not because they were left out, but because the store exposes no such
  operation and the database grants none. A correction is a new version.

Activation takes ``expected_previous_version_number`` in the request body; the
store turns it into a compare-and-swap, and a conflict is a 409 with
``code: "activation_conflict"`` telling the caller to re-read, never a silent
overwrite.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import Principal, require_permission
from app.auth.permissions import Permission
from app.configs.rules import MAX_BODY_CHARS, SLOT_NAME_PATTERN, ConfigKind
from app.core.errors import DependencyUnavailableError
from app.services.config_versions import (
    ActivationView,
    ConfigVersionStore,
    ConfigVersionView,
    RevealedBody,
    VersionDiffView,
)

router = APIRouter(prefix="/api/v1/configs", tags=["configs"])

# Creating a version is provider-configuration administration; activating one is
# a Category-B-adjacent change of live behaviour, so it needs the separate
# approval authority. Reads follow the Day 013 precedent (no dedicated
# read:configs verb in matrix v1, so read:requests is the sanctioned capability).
AuthorDep = Annotated[Principal, Depends(require_permission(Permission.MANAGE_PROVIDER_CONFIG))]
ApproverDep = Annotated[Principal, Depends(require_permission(Permission.APPROVE_CONFIG_CHANGE))]
ReaderDep = Annotated[Principal, Depends(require_permission(Permission.READ_REQUEST_HISTORY))]

KindPath = Annotated[ConfigKind, Path()]
SlotPath = Annotated[str, Path(pattern=SLOT_NAME_PATTERN, max_length=128)]


def get_config_version_store(request: Request) -> ConfigVersionStore:
    store = getattr(request.app.state, "config_version_store", None)
    if store is None:
        # Fail closed exactly like the Day 012/013 dependencies: a missing
        # store is an outage, never an unauthenticated free pass.
        raise DependencyUnavailableError("Configuration version store is unavailable.")
    return store  # type: ignore[no-any-return]


ConfigStoreDep = Annotated[ConfigVersionStore, Depends(get_config_version_store)]

# Shared parameterless Query singletons (B008-clean): defaults live in the
# function signatures, validation constraints live here.
CursorQuery = Query(max_length=512)
LimitQuery = Query(ge=1, le=100)
DiffQuery = Query()


# ---------------------------------------------------------------------------
# Request / response contracts
# ---------------------------------------------------------------------------


class ConfigVersionCreateRequest(BaseModel):
    """One proposed version. ``payload`` is schema-checked per ``kind``.

    ``body`` is accepted here and *only* here: it is sealed on the way in and
    never echoed back on the way out.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ConfigKind
    name: str = Field(min_length=1, max_length=128, pattern=SLOT_NAME_PATTERN)
    payload: dict[str, Any]
    body: str | None = Field(default=None, max_length=MAX_BODY_CHARS)
    secret_ref: str | None = Field(default=None, min_length=1, max_length=500)


class ActivationCreateRequest(BaseModel):
    """Compare-and-swap activation of an existing version."""

    model_config = ConfigDict(extra="forbid")

    version_id: uuid.UUID
    # None (or 0) asserts "nothing is active yet"; any other value asserts the
    # exact number the caller last read.
    expected_previous_version_number: int | None = Field(default=None, ge=0, le=1_000_000)
    qualification: dict[str, Any] | None = None
    is_last_known_good: bool = False


class ConfigVersionResponse(BaseModel):
    id: str
    organization_id: str
    kind: str
    name: str
    version_number: int
    content_hash: str
    payload: dict[str, Any]
    body_storage: str
    secret_ref_fingerprint: str | None
    created_by_user_id: str
    created_at: str


class ConfigVersionPage(BaseModel):
    items: list[ConfigVersionResponse]
    next_cursor: str | None


class DiffEntryResponse(BaseModel):
    path: str
    change: str
    before: Any = None
    after: Any = None


class VersionDiffResponse(BaseModel):
    kind: str
    name: str
    before_version_id: str
    after_version_id: str
    before_version_number: int
    after_version_number: int
    before_content_hash: str
    after_content_hash: str
    identical: bool
    body_change_unverified: bool
    entries: list[DiffEntryResponse]


class ActivationResponse(BaseModel):
    id: str
    organization_id: str
    kind: str
    name: str
    version_id: str
    version_number: int
    expected_previous_number: int | None
    supersedes_id: str | None
    activated_by_user_id: str
    activated_at: str
    superseded_at: str | None
    qualification: dict[str, Any] | None
    is_last_known_good: bool
    is_active: bool


class ActivationPage(BaseModel):
    items: list[ActivationResponse]
    next_cursor: str | None


class RevealedBodyResponse(BaseModel):
    version_id: str
    version_number: int
    content_hash: str
    body: str


def _respond_version(view: ConfigVersionView) -> ConfigVersionResponse:
    return ConfigVersionResponse(
        id=str(view.id),
        organization_id=str(view.organization_id),
        kind=view.kind,
        name=view.name,
        version_number=view.version_number,
        content_hash=view.content_hash,
        payload=view.payload,
        body_storage=view.body_storage,
        secret_ref_fingerprint=view.secret_ref_fingerprint,
        created_by_user_id=str(view.created_by_user_id),
        created_at=view.created_at.isoformat(),
    )


def _respond_activation(view: ActivationView) -> ActivationResponse:
    return ActivationResponse(
        id=str(view.id),
        organization_id=str(view.organization_id),
        kind=view.kind,
        name=view.name,
        version_id=str(view.version_id),
        version_number=view.version_number,
        expected_previous_number=view.expected_previous_number,
        supersedes_id=str(view.supersedes_id) if view.supersedes_id else None,
        activated_by_user_id=str(view.activated_by_user_id),
        activated_at=view.activated_at.isoformat(),
        superseded_at=view.superseded_at.isoformat() if view.superseded_at else None,
        qualification=view.qualification,
        is_last_known_good=view.is_last_known_good,
        is_active=view.is_active,
    )


def _respond_diff(diff: VersionDiffView) -> VersionDiffResponse:
    return VersionDiffResponse(
        kind=diff.kind,
        name=diff.name,
        before_version_id=str(diff.before_version_id),
        after_version_id=str(diff.after_version_id),
        before_version_number=diff.before_version_number,
        after_version_number=diff.after_version_number,
        before_content_hash=diff.before_content_hash,
        after_content_hash=diff.after_content_hash,
        identical=diff.identical,
        body_change_unverified=diff.body_change_unverified,
        entries=[
            DiffEntryResponse(
                path=entry.path, change=entry.change, before=entry.before, after=entry.after
            )
            for entry in diff.entries
        ],
    )


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


@router.post(
    "/versions",
    response_model=ConfigVersionResponse,
    status_code=201,
    summary="Propose an immutable provider-route or prompt version",
    responses={
        409: {"description": "Another writer claimed this ladder position first."},
        422: {"description": "Payload rejected by the kind's closed schema."},
    },
)
async def create_version(
    payload: ConfigVersionCreateRequest,
    principal: AuthorDep,
    store: ConfigStoreDep,
    request: Request,
) -> ConfigVersionResponse:
    view = await store.create_version(
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        kind=payload.kind.value,
        name=payload.name,
        payload=payload.payload,
        body=payload.body,
        secret_ref=payload.secret_ref,
        request_id=getattr(request.state, "request_id", None),
    )
    return _respond_version(view)


@router.get(
    "/versions/{version_id}",
    response_model=ConfigVersionResponse,
    summary="Fetch one version (metadata only — never a sealed body)",
)
async def get_version(
    version_id: uuid.UUID,
    principal: ReaderDep,
    store: ConfigStoreDep,
) -> ConfigVersionResponse:
    view = await store.get_version(organization_id=principal.organization_id, version_id=version_id)
    return _respond_version(view)


@router.get(
    "/{kind}/{name}/versions",
    response_model=ConfigVersionPage,
    summary="List a slot's version ladder, newest first",
)
async def list_versions(
    kind: KindPath,
    name: SlotPath,
    principal: ReaderDep,
    store: ConfigStoreDep,
    cursor: Annotated[str | None, CursorQuery] = None,
    limit: Annotated[int, LimitQuery] = 20,
) -> ConfigVersionPage:
    page = await store.list_versions(
        organization_id=principal.organization_id,
        kind=kind.value,
        name=name,
        cursor=cursor,
        limit=limit,
    )
    return ConfigVersionPage(
        items=[_respond_version(view) for view in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/{kind}/{name}/diff",
    response_model=VersionDiffResponse,
    summary="Redacted diff between two versions of the same slot",
    responses={422: {"description": "The two versions belong to different slots."}},
)
async def diff_versions(
    kind: KindPath,
    name: SlotPath,
    before: Annotated[uuid.UUID, DiffQuery],
    after: Annotated[uuid.UUID, DiffQuery],
    principal: ReaderDep,
    store: ConfigStoreDep,
) -> VersionDiffResponse:
    diff = await store.diff_versions(
        organization_id=principal.organization_id,
        before_version_id=before,
        after_version_id=after,
    )
    return _respond_diff(diff)


@router.post(
    "/versions/{version_id}/reveal-body",
    response_model=RevealedBodyResponse,
    summary="Open a sealed prompt body (privileged and audit-recorded)",
    responses={
        409: {"description": "This version's body is stored externally by reference."},
        503: {"description": "The stored body could not be decrypted."},
    },
)
async def reveal_body(
    version_id: uuid.UUID,
    principal: AuthorDep,
    store: ConfigStoreDep,
    request: Request,
) -> RevealedBodyResponse:
    revealed: RevealedBody = await store.reveal_body(
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        version_id=version_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return RevealedBodyResponse(
        version_id=str(revealed.version_id),
        version_number=revealed.version_number,
        content_hash=revealed.content_hash,
        body=revealed.body,
    )


# ---------------------------------------------------------------------------
# Activation (compare-and-swap)
# ---------------------------------------------------------------------------


@router.post(
    "/{kind}/{name}/activations",
    response_model=ActivationResponse,
    status_code=201,
    summary="Activate a version, conditional on the expected previous one",
    responses={
        409: {
            "description": "activation_conflict, incompatible_configuration "
            "or version_slot_mismatch — nothing was changed."
        },
        422: {"description": "Last-known-good claimed without qualification evidence."},
    },
)
async def create_activation(
    kind: KindPath,
    name: SlotPath,
    payload: ActivationCreateRequest,
    principal: ApproverDep,
    store: ConfigStoreDep,
    request: Request,
) -> ActivationResponse:
    view = await store.activate(
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        kind=kind.value,
        name=name,
        version_id=payload.version_id,
        expected_previous_number=payload.expected_previous_version_number,
        qualification=payload.qualification,
        is_last_known_good=payload.is_last_known_good,
        request_id=getattr(request.state, "request_id", None),
    )
    return _respond_activation(view)


@router.get(
    "/{kind}/{name}/activation",
    response_model=ActivationResponse,
    summary="Read the slot's currently active version",
)
async def get_active(
    kind: KindPath,
    name: SlotPath,
    principal: ReaderDep,
    store: ConfigStoreDep,
) -> ActivationResponse:
    view = await store.get_active(
        organization_id=principal.organization_id, kind=kind.value, name=name
    )
    return _respond_activation(view)


@router.get(
    "/{kind}/{name}/last-known-good",
    response_model=ActivationResponse,
    summary="Newest activation that earned last-known-good status",
    responses={404: {"description": "No activation carries qualification evidence yet."}},
)
async def get_last_known_good(
    kind: KindPath,
    name: SlotPath,
    principal: ReaderDep,
    store: ConfigStoreDep,
) -> ActivationResponse:
    view = await store.get_last_known_good(
        organization_id=principal.organization_id, kind=kind.value, name=name
    )
    return _respond_activation(view)


@router.get(
    "/{kind}/{name}/activations",
    response_model=ActivationPage,
    summary="Read a slot's full activation history, newest first",
)
async def list_activations(
    kind: KindPath,
    name: SlotPath,
    principal: ReaderDep,
    store: ConfigStoreDep,
    cursor: Annotated[str | None, CursorQuery] = None,
    limit: Annotated[int, LimitQuery] = 20,
) -> ActivationPage:
    page = await store.list_activations(
        organization_id=principal.organization_id,
        kind=kind.value,
        name=name,
        cursor=cursor,
        limit=limit,
    )
    return ActivationPage(
        items=[_respond_activation(view) for view in page.items],
        next_cursor=page.next_cursor,
    )


__all__ = ["get_config_version_store", "router"]
