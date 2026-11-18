"""FastAPI authentication & authorization dependencies (Day 012).

Layering, in the order the step mandates:

1. **Authenticate** — verify the access token's signature/expiry/issuer/audience
   and type (:func:`app.auth.jwt.decode_token`).
2. **Resolve organization & membership server-side** — re-read the caller's role
   from the database under RLS (:meth:`AuthRepository.get_membership`). The JWT
   ``role`` claim is *never* trusted on its own; a withdrawn membership or a
   re-assigned role takes effect on the very next request.
3. **Authorize** — check the resolved role against the versioned permission
   matrix (:mod:`app.auth.permissions`).

:func:`require_permission` composes all three into a route dependency. The
stateful (cookie-mutating) endpoints additionally pass
:func:`verify_stateful_request`, the CSRF/origin defence for the refresh cookie.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.errors import AuthenticationError, PermissionDeniedError
from app.auth.jwt import TokenError, decode_token
from app.auth.permissions import Permission, has_permission
from app.auth.repository import AuthRepository
from app.auth.roles import Role
from app.auth.security import constant_time_equals
from app.core.settings import Settings

# auto_error=False so a missing header becomes our envelope-wrapped 401, not
# FastAPI's bare {"detail": ...} HTTPException.
_bearer = HTTPBearer(auto_error=False)
# ``Annotated`` form keeps the B008 lint clean while injecting the bearer token.
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated, membership-verified caller."""

    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: Role


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_repository(request: Request) -> AuthRepository:
    repo = getattr(request.app.state, "auth_repository", None)
    if repo is None:
        # The store is a configured runtime dependency; if lifespan could not
        # build it, fail closed rather than silently accepting any token.
        raise AuthenticationError("authentication service is unavailable")
    return repo  # type: ignore[no-any-return]


async def authenticated_principal(
    request: Request,
    credentials: BearerDep,
) -> Principal:
    """Route dependency for any-authenticated access (no specific permission)."""
    return await get_current_principal(request, credentials)


async def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = None,
) -> Principal:
    """Resolve the bearer access token to a live, membership-checked principal."""
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer access token.")
    settings = get_settings(request)
    try:
        claims = decode_token(settings, credentials.credentials, expected_type="access")
    except TokenError as exc:
        raise AuthenticationError("Invalid or expired access token.") from exc

    repository = get_repository(request)
    membership = await repository.get_membership(
        user_id=claims.subject, organization_id=claims.organization_id
    )
    # Authoritative role comes from the database, never the token claim.
    if membership is None or not membership.is_active:
        raise AuthenticationError("Active membership not found for this principal.")
    try:
        role = Role(membership.role)
    except ValueError as exc:  # corrupt role string -> deny, never guess
        raise AuthenticationError("Principal role is not recognized.") from exc
    return Principal(
        user_id=membership.user_id,
        organization_id=membership.organization_id,
        role=role,
    )


def require_permission(
    permission: Permission,
) -> Callable[..., Awaitable[Principal]]:
    """Dependency factory: authenticate, resolve membership, then authorize.

    Usage::

        @router.get("/something")
        async def handler(principal: Principal = Depends(require_permission(
            Permission.READ_TELEMETRY))): ...
    """

    async def dependency(
        request: Request,
        credentials: BearerDep,
    ) -> Principal:
        principal = await get_current_principal(request, credentials)
        if not has_permission(principal.role, permission):
            raise PermissionDeniedError("Your role does not grant this action.")
        return principal

    return dependency


def verify_stateful_request(request: Request, settings: Settings) -> None:
    """CSRF + origin defence for credentialed cookie mutations (refresh/logout).

    Two independent checks, both required:

    * **Origin allow-list** — the request's ``Origin`` must be present and match
      an explicitly configured origin. A cross-site attacker's page has a foreign
      origin, so its credentialed request is rejected even though the browser
      would otherwise attach the HttpOnly cookie.
    * **Double-submit CSRF token** — the ``X-CSRF-Token`` header must equal the
      readable CSRF cookie. Only same-origin JavaScript can read that cookie and
      echo it back, proving the request came from our own client, not a forged
      form.
    """
    origin = request.headers.get("origin")
    if origin is None or origin not in settings.allowed_origin_set:
        raise PermissionDeniedError("Request origin is not permitted for this operation.")

    header_token = request.headers.get(settings.csrf_header_name)
    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    if not header_token or not cookie_token or not constant_time_equals(header_token, cookie_token):
        raise PermissionDeniedError("Missing or mismatched CSRF token.")


__all__ = [
    "Principal",
    "authenticated_principal",
    "get_current_principal",
    "get_repository",
    "get_settings",
    "require_permission",
    "verify_stateful_request",
]
