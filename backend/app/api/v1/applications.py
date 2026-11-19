"""Tenant application CRUD endpoints (Day 013).

Boundary rules realized here:

* The tenant is **always** ``principal.organization_id`` — resolved from the
  access token *and* re-checked against live membership by the Day 012
  dependency chain. No client header, body field or query parameter can pick
  an organization.
* Every mutation requires the ADR-0004 matrix permission
  (``administer:applications`` = Owner/Admin) and is recorded in the
  append-only trail **inside the same transaction** by the store — the route
  never writes audit rows itself and therefore can't forget to.
* Foreign and nonexistent ids answer the identical 404 envelope: the surface
  leaks no existence information across tenants.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import Principal, require_permission
from app.auth.permissions import Permission
from app.core.errors import DependencyUnavailableError
from app.services.applications import ApplicationStore, ApplicationView

router = APIRouter(prefix="/api/v1/applications", tags=["applications"])

ManagerDep = Annotated[Principal, Depends(require_permission(Permission.MANAGE_APPLICATIONS))]
# Matrix v1 has no dedicated read:applications verb, so dashboard reads map
# to its closest sanctioned capability, read:requests (ADR-0004) — every
# ``read`` permission holder (all four roles) may list/detail applications.
# A future matrix version bump may split this out explicitly.
ReaderDep = Annotated[Principal, Depends(require_permission(Permission.READ_REQUEST_HISTORY))]


def get_application_store(request: Request) -> ApplicationStore:
    store = getattr(request.app.state, "application_store", None)
    if store is None:
        # Fail closed exactly like the Day 012 repository dependency: a
        # missing store is an outage, never an unauthenticated free pass.
        raise DependencyUnavailableError("Application store is unavailable.")
    return store  # type: ignore[no-any-return]


StoreDep = Annotated[ApplicationStore, Depends(get_application_store)]

# Shared parameterless Query singletons (B008-clean): defaults live in the
# function signatures, validation constraints live here.
CursorQuery = Query(max_length=512)
LimitQuery = Query(ge=1, le=100)


# ---------------------------------------------------------------------------
# Request / response contracts
# ---------------------------------------------------------------------------
class ApplicationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class ApplicationUpdateRequest(BaseModel):
    """Partial update; omitted fields stay untouched."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    status: Literal["active", "archived"] | None = None


class ApplicationResponse(BaseModel):
    id: str
    organization_id: str
    owner_user_id: str
    name: str
    description: str | None
    status: str
    created_at: str
    updated_at: str


class ApplicationPage(BaseModel):
    items: list[ApplicationResponse]
    next_cursor: str | None


def _respond(view: ApplicationView) -> ApplicationResponse:
    return ApplicationResponse(
        id=str(view.id),
        organization_id=str(view.organization_id),
        owner_user_id=str(view.owner_user_id),
        name=view.name,
        description=view.description,
        status=view.status,
        created_at=view.created_at.isoformat(),
        updated_at=view.updated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=ApplicationResponse,
    status_code=201,
    summary="Create a gateway application",
    responses={409: {"description": "Name already used in this organization."}},
)
async def create_application(
    payload: ApplicationCreateRequest,
    principal: ManagerDep,
    store: StoreDep,
    request: Request,
) -> ApplicationResponse:
    view = await store.create_application(
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        name=payload.name,
        description=payload.description,
        request_id=getattr(request.state, "request_id", None),
    )
    return _respond(view)


@router.get("", response_model=ApplicationPage, summary="List applications (cursor-paginated)")
async def list_applications(
    principal: ReaderDep,
    store: StoreDep,
    cursor: Annotated[str | None, CursorQuery] = None,
    limit: Annotated[int, LimitQuery] = 20,
) -> ApplicationPage:
    page = await store.list_applications(
        organization_id=principal.organization_id, cursor=cursor, limit=limit
    )
    return ApplicationPage(
        items=[_respond(view) for view in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/{application_id}", response_model=ApplicationResponse, summary="Fetch one application"
)
async def get_application(
    application_id: uuid.UUID,
    principal: ReaderDep,
    store: StoreDep,
) -> ApplicationResponse:
    view = await store.get_application(
        organization_id=principal.organization_id, application_id=application_id
    )
    return _respond(view)


@router.patch(
    "/{application_id}",
    response_model=ApplicationResponse,
    summary="Rename, describe or activate/archive an application",
    responses={404: {"description": "No such application in this organization."}},
)
async def update_application(
    application_id: uuid.UUID,
    payload: ApplicationUpdateRequest,
    principal: ManagerDep,
    store: StoreDep,
    request: Request,
) -> ApplicationResponse:
    view = await store.update_application(
        organization_id=principal.organization_id,
        application_id=application_id,
        actor_user_id=principal.user_id,
        name=payload.name,
        description=payload.description,
        status=payload.status,
        request_id=getattr(request.state, "request_id", None),
    )
    return _respond(view)


__all__ = ["get_application_store", "router"]
