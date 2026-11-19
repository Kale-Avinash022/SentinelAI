"""Transactional append-only audit writer (Day 013).

:func:`append_audit` inserts an :class:`~app.models.audit.AuditLog` row into
**the caller's already-open session** — never its own transaction. That is
what makes the trail atomic with the protected mutation: the mutation and its
audit row commit together or vanish together.

Defense in depth against secret leakage:

1. :func:`serialize_detail` refuses any detail value containing the
   ``sk_live_`` marker (raw *or* display prefix), so a mistake at a call site
   fails loudly in Python;
2. the ``ck_audit_logs_detail_not_raw_key`` CHECK repeats the same rule in
   the database, so even a writer that skips this module cannot persist one;
3. the API role has no UPDATE/DELETE grant at all, so nothing can rewrite
   history afterwards.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.keys import API_KEY_PREFIX
from app.models.audit import AuditActorType, AuditLog


class AuditAction(StrEnum):
    """Stable, machine-readable event names for credential-lifecycle changes."""

    APPLICATION_CREATED = "application.created"
    APPLICATION_UPDATED = "application.updated"
    API_KEY_CREATED = "api_key.created"
    API_KEY_REVOKED = "api_key.revoked"
    API_KEY_ROTATED = "api_key.rotated"


class AuditResource(StrEnum):
    """Entity kinds that appear in the trail."""

    APPLICATION = "application"
    API_KEY = "api_key"


def serialize_detail(detail: Mapping[str, Any] | None) -> str | None:
    """Canonical JSON for the audit ``detail`` column, secret-checked.

    Only metadata is ever passed here (names, prefixes, scope lists, status
    flips). Any string transitively containing the raw-key marker aborts the
    write with ``ValueError`` — the caller's whole transaction rolls back, so
    a leaking bug cannot even leave the mutation behind without its trail.
    """
    if detail is None:
        return None
    payload = dict(detail)
    _assert_secret_free(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _assert_secret_free(value: Any) -> None:
    if isinstance(value, Mapping):
        for item in value.values():
            _assert_secret_free(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _assert_secret_free(item)
    elif isinstance(value, str) and API_KEY_PREFIX in value:
        raise ValueError("audit detail must never contain API key material")


def append_audit(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor_type: AuditActorType,
    actor_id: uuid.UUID | None,
    action: AuditAction | str,
    resource_type: AuditResource | str,
    resource_id: uuid.UUID,
    detail: Mapping[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditLog:
    """Insert one audit row into ``session``'s open transaction.

    The row's ``organization_id`` is supplied by the caller's *authenticated
    tenant context* — the same value the RLS ``WITH CHECK`` polices, so a
    forged cross-tenant audit entry is rejected by the database itself.
    """
    row = AuditLog(
        organization_id=organization_id,
        actor_type=AuditActorType(actor_type).value,
        actor_id=actor_id,
        action=str(action),
        resource_type=str(resource_type),
        resource_id=resource_id,
        detail=serialize_detail(detail),
        request_id=request_id,
    )
    session.add(row)
    return row


__all__ = [
    "AuditAction",
    "AuditResource",
    "append_audit",
    "serialize_detail",
]
