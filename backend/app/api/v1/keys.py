"""Scoped API-key lifecycle endpoints (Day 013).

Create / list / revoke / rotate for machine credentials, Owner-guarded per
ADR-0004 (``administer:api-keys``). The contract the acceptance checklist
pins down:

* **Raw key shown exactly once** — only the create and rotate responses carry
  ``api_key``; list/detail/read responses are built from fields that have no
  secret to leak. The trail stores metadata without even the display prefix.
* **Revocation is immediate** — one UPDATE sets ``revoked_at``; the key dies
  on the next check, and the row survives (no DELETE grant) as evidence.
* **Rotation overlaps deliberately** — the predecessor keeps working until
  ``min(its expiry, now + API_KEY_ROTATION_GRACE_SECONDS)``, then expires.
* Tenant scoping comes from the authenticated principal only; foreign
  application/key ids are the standard indistinguishable 404.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.applications import StoreDep
from app.auth.dependencies import Principal, get_settings, require_permission
from app.auth.permissions import Permission
from app.core.settings import Settings
from app.models.api_key import KeyScope
from app.services.applications import ApiKeyView, IssuedApiKey

router = APIRouter(tags=["api-keys"])

KeysAdminDep = Annotated[Principal, Depends(require_permission(Permission.MANAGE_API_KEYS))]
SettingsDep = Annotated[Settings, Depends(get_settings)]

# Shared parameterless Query singletons (B008-clean): defaults live in the
# function signatures, validation constraints live here.
CursorQuery = Query(max_length=512)
LimitQuery = Query(ge=1, le=100)

_KEY_COLLECTION = "/api/v1/applications/{application_id}/api-keys"


# ---------------------------------------------------------------------------
# Request / response contracts
# ---------------------------------------------------------------------------
class ApiKeyCreateRequest(BaseModel):
    """``scopes`` is typed against the closed KeyScope enum: anything else is
    a schema-level 422, never a silent grant."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    scopes: list[KeyScope] = Field(min_length=1, max_length=4)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class ApiKeyResponse(BaseModel):
    id: str
    application_id: str
    name: str
    key_prefix: str
    scopes: list[str]
    state: str
    created_at: str
    expires_at: str | None
    revoked_at: str | None
    superseded_by_id: str | None


class ApiKeyCreatedResponse(ApiKeyResponse):
    """Adds the raw secret — the one and only time it is ever transmitted."""

    api_key: str


class ApiKeyPage(BaseModel):
    items: list[ApiKeyResponse]
    next_cursor: str | None


class ApiKeyRotatedResponse(BaseModel):
    """Successor carries its raw key once; predecessor shows its overlap."""

    successor: ApiKeyCreatedResponse
    predecessor: ApiKeyResponse


def _respond(view: ApiKeyView) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=str(view.id),
        application_id=str(view.application_id),
        name=view.name,
        key_prefix=view.key_prefix,
        scopes=sorted(view.scopes),
        state=view.state.value,
        created_at=view.created_at.isoformat(),
        expires_at=view.expires_at.isoformat() if view.expires_at else None,
        revoked_at=view.revoked_at.isoformat() if view.revoked_at else None,
        superseded_by_id=str(view.superseded_by_id) if view.superseded_by_id else None,
    )


def _respond_issued(issued: IssuedApiKey) -> ApiKeyCreatedResponse:
    return ApiKeyCreatedResponse(**_respond(issued.view).model_dump(), api_key=issued.secret)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post(
    _KEY_COLLECTION,
    response_model=ApiKeyCreatedResponse,
    status_code=201,
    summary="Issue a new scoped API key (raw value returned once)",
    responses={404: {"description": "No such application in this organization."}},
)
async def create_api_key(
    application_id: uuid.UUID,
    payload: ApiKeyCreateRequest,
    principal: KeysAdminDep,
    store: StoreDep,
    request: Request,
) -> ApiKeyCreatedResponse:
    issued = await store.create_api_key(
        organization_id=principal.organization_id,
        application_id=application_id,
        actor_user_id=principal.user_id,
        name=payload.name,
        # Enum values -> canonical strings; the store re-validates the allow-list.
        scopes=[scope.value for scope in payload.scopes],
        expires_in_days=payload.expires_in_days,
        request_id=getattr(request.state, "request_id", None),
    )
    return _respond_issued(issued)


@router.get(
    _KEY_COLLECTION,
    response_model=ApiKeyPage,
    summary="List key metadata (never secrets or hashes)",
)
async def list_api_keys(
    application_id: uuid.UUID,
    principal: KeysAdminDep,
    store: StoreDep,
    cursor: Annotated[str | None, CursorQuery] = None,
    limit: Annotated[int, LimitQuery] = 20,
) -> ApiKeyPage:
    page = await store.list_api_keys(
        organization_id=principal.organization_id,
        application_id=application_id,
        cursor=cursor,
        limit=limit,
    )
    return ApiKeyPage(items=[_respond(view) for view in page.items], next_cursor=page.next_cursor)


@router.post(
    _KEY_COLLECTION + "/{key_id}/revoke",
    response_model=ApiKeyResponse,
    summary="Revoke a key immediately (row retained as evidence)",
)
async def revoke_api_key(
    application_id: uuid.UUID,
    key_id: uuid.UUID,
    principal: KeysAdminDep,
    store: StoreDep,
    request: Request,
) -> ApiKeyResponse:
    view = await store.revoke_api_key(
        organization_id=principal.organization_id,
        application_id=application_id,
        key_id=key_id,
        actor_user_id=principal.user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return _respond(view)


@router.post(
    _KEY_COLLECTION + "/{key_id}/rotate",
    response_model=ApiKeyRotatedResponse,
    summary="Rotate a key: successor shown once, predecessor gets a bounded overlap",
    responses={409: {"description": "Revoked keys cannot be rotated."}},
)
async def rotate_api_key(
    application_id: uuid.UUID,
    key_id: uuid.UUID,
    principal: KeysAdminDep,
    store: StoreDep,
    settings: SettingsDep,
    request: Request,
) -> ApiKeyRotatedResponse:
    successor, predecessor = await store.rotate_api_key(
        organization_id=principal.organization_id,
        application_id=application_id,
        key_id=key_id,
        actor_user_id=principal.user_id,
        grace_seconds=settings.api_key_rotation_grace_seconds,
        request_id=getattr(request.state, "request_id", None),
    )
    return ApiKeyRotatedResponse(
        successor=_respond_issued(successor),
        predecessor=_respond(predecessor),
    )


__all__ = ["router"]
