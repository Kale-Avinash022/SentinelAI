"""Append-only audit trail read endpoint (Day 013).

The trail is *written* transactionally by the store (see
:mod:`app.services.audit`); this router only exposes the accountable read
side, guarded by the matrix's ``read:audit`` capability (granted to every
role) and hard-scoped to the caller's organization — RLS enforces the same
boundary the query filter does, and the API role has no UPDATE/DELETE grant
anywhere near the table, so this surface is provably read-only.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.v1.applications import StoreDep
from app.auth.dependencies import Principal, require_permission
from app.auth.permissions import Permission
from app.services.applications import AuditView

router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit"])

AuditReaderDep = Annotated[Principal, Depends(require_permission(Permission.READ_AUDIT_LOGS))]

# Shared parameterless Query singletons (B008-clean): defaults live in the
# function signatures, validation constraints live here.
CursorQuery = Query(max_length=512)
LimitQuery = Query(ge=1, le=200)
ResourceTypeQuery = Query(max_length=48)
ResourceIdQuery = Query()


class AuditLogResponse(BaseModel):
    id: str
    actor_type: str
    actor_id: str | None
    action: str
    resource_type: str
    resource_id: str
    detail: dict[str, object] | None
    request_id: str | None
    created_at: str


class AuditLogPage(BaseModel):
    items: list[AuditLogResponse]
    next_cursor: str | None


def _respond(view: AuditView) -> AuditLogResponse:
    return AuditLogResponse(
        id=str(view.id),
        actor_type=view.actor_type,
        actor_id=str(view.actor_id) if view.actor_id else None,
        action=view.action,
        resource_type=view.resource_type,
        resource_id=str(view.resource_id),
        detail=view.detail,
        request_id=view.request_id,
        created_at=view.created_at.isoformat(),
    )


@router.get(
    "",
    response_model=AuditLogPage,
    summary="Read the tenant's append-only audit trail (newest first)",
)
async def list_audit_logs(
    principal: AuditReaderDep,
    store: StoreDep,
    cursor: Annotated[str | None, CursorQuery] = None,
    limit: Annotated[int, LimitQuery] = 50,
    resource_type: Annotated[str | None, ResourceTypeQuery] = None,
    resource_id: Annotated[uuid.UUID | None, ResourceIdQuery] = None,
) -> AuditLogPage:
    page = await store.list_audit_logs(
        organization_id=principal.organization_id,
        cursor=cursor,
        limit=limit,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    return AuditLogPage(items=[_respond(view) for view in page.items], next_cursor=page.next_cursor)


__all__ = ["router"]
