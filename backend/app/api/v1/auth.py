"""Human authentication endpoints: login, refresh, logout, current-user (Day 012).

Security contract realized here (see steps/step-012.md):

* **Login** verifies an Argon2id password and returns a short-lived JWT *access
  token in the JSON body* (for memory-only SPA storage — never localStorage),
  while the longer-lived *refresh token* is delivered only as an
  ``HttpOnly``/``SameSite=lax``/``Secure``-in-prod cookie. Failures are one
  generic ``invalid_credentials`` envelope regardless of whether the email or the
  password was wrong (no enumeration), and a fixed-window throttle blunts
  brute force.
* **Refresh** rotates the refresh token on every use (single-use). Replaying an
  already-rotated token is treated as theft and revokes the whole family.
  Refresh and logout are mutating credentialed requests, so both require the
  origin allow-list *and* the double-submit CSRF header.
* **Logout** clears the family and the cookies.
* **Current-user** authenticates and reports the caller's live, DB-resolved
  role and derived permissions.

No plaintext password, refresh token, or signing key is ever logged: the request
logger is metadata-only and every secret here is a ``SecretStr`` or an opaque
cookie value.
"""

from __future__ import annotations

import secrets
import uuid
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.auth.dependencies import (
    Principal,
    authenticated_principal,
    get_repository,
    get_settings,
    verify_stateful_request,
)
from app.auth.errors import (
    AuthenticationError,
    InvalidCredentialsError,
    RateLimitedError,
)
from app.auth.jwt import (
    TokenClaims,
    TokenError,
    decode_token,
    issue_access_token,
    issue_refresh_token,
)
from app.auth.permissions import permissions_for
from app.auth.rate_limit import LoginRateLimiter
from app.auth.repository import AuthRepository, RotateStatus
from app.auth.security import (
    hash_password,
    needs_rehash,
    token_digest,
    verify_password,
)
from app.core.settings import Settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# ``Annotated`` dependency aliases (rather than ``= Depends(...)`` defaults) so
# the B008 lint stays clean and the injections read as part of each signature.
SettingsDep = Annotated[Settings, Depends(get_settings)]
RepositoryDep = Annotated[AuthRepository, Depends(get_repository)]
PrincipalDep = Annotated[Principal, Depends(authenticated_principal)]


# ---------------------------------------------------------------------------
# Request / response contracts
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    """Strict login payload. ``password`` is a ``SecretStr`` so it never lands in
    a ``repr``/log; extra keys are forbidden."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024)


class TokenPair(BaseModel):
    """Access token returned to memory-only client storage.

    ``csrf_token`` is echoed in the body *and* set as a readable cookie so the
    SPA can replay it in the ``X-CSRF-Token`` header on the next refresh.
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    csrf_token: str


class MeResponse(BaseModel):
    user_id: str
    organization_id: str
    role: str
    permissions: list[str]


class StatusResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A real Argon2 digest used to equalize timing when no user matches.

    Hashing a random throwaway value means a wrong-email attempt spends the same
    Argon2 budget as a wrong-password attempt, so response timing cannot be used
    to enumerate accounts.
    """
    return hash_password(secrets.token_urlsafe(24))


def _get_limiter(request: Request) -> LoginRateLimiter:
    limiter = getattr(request.app.state, "login_rate_limiter", None)
    if limiter is None:  # pragma: no cover - lifespan always builds one
        raise RateLimitedError("Login service unavailable.")
    return limiter  # type: ignore[no-any-return]


def _set_session_cookies(
    response: Response,
    settings: Settings,
    *,
    refresh_token: str,
    csrf_token: str,
    max_age: int,
) -> None:
    secure = settings.secure_cookies
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=max_age,
        path="/api/v1/auth",
        httponly=True,
        secure=secure,
        samesite="lax",
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        max_age=max_age,
        path="/",
        httponly=False,  # readable by same-origin JS for the double-submit token
        secure=secure,
        samesite="lax",
    )


def _clear_session_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.refresh_cookie_name, path="/api/v1/auth", samesite="lax")
    response.delete_cookie(settings.csrf_cookie_name, path="/", samesite="lax")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------
@router.post(
    "/login",
    response_model=TokenPair,
    summary="Authenticate a dashboard user",
    responses={
        401: {"description": "Invalid credentials."},
        429: {"description": "Too many login attempts."},
    },
)
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    settings: SettingsDep,
    repository: RepositoryDep,
) -> TokenPair:
    email = payload.email.strip().lower()
    limiter = _get_limiter(request)
    decision = limiter.check((_client_ip(request), email))
    if not decision.allowed:
        raise RateLimitedError("Too many login attempts; try again later.")

    identity = await repository.lookup_login_identity(email)
    password = payload.password.get_secret_value()

    if identity is None:
        # Equalize work against a real hash, then return the same generic error.
        verify_password(password, _dummy_hash())
        raise InvalidCredentialsError("Invalid email or password.")

    if not verify_password(password, identity.password_hash):
        raise InvalidCredentialsError("Invalid email or password.")

    # Correct password but disabled account: indistinguishable from bad creds.
    if not identity.is_active:
        raise InvalidCredentialsError("Invalid email or password.")

    # The authoritative role comes from the database, never from a client claim.
    membership = await repository.get_membership(
        user_id=identity.user_id, organization_id=identity.organization_id
    )
    if membership is None or not membership.is_active:
        raise InvalidCredentialsError("Invalid email or password.")

    # Transparently migrate a verified password to current Argon2 costs.
    if identity.password_hash and needs_rehash(identity.password_hash):
        await repository.update_password_hash(
            user_id=identity.user_id,
            organization_id=identity.organization_id,
            password_hash=hash_password(password),
        )

    limiter.reset((_client_ip(request), email))

    family_id = uuid.uuid4()
    refresh_token, _jti, refresh_exp = issue_refresh_token(
        settings,
        user_id=identity.user_id,
        organization_id=identity.organization_id,
        family=family_id,
    )
    await repository.create_session(
        user_id=identity.user_id,
        organization_id=identity.organization_id,
        family_id=family_id,
        token_hash=token_digest(refresh_token),
        expires_at=refresh_exp,
    )
    access_token, _access_exp = issue_access_token(
        settings,
        user_id=identity.user_id,
        organization_id=identity.organization_id,
        role=membership.role,
    )
    csrf_token = secrets.token_urlsafe(32)
    _set_session_cookies(
        response,
        settings,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
        max_age=settings.refresh_token_ttl_seconds,
    )
    return TokenPair(
        access_token=access_token,
        expires_in=settings.access_token_ttl_seconds,
        csrf_token=csrf_token,
    )


# ---------------------------------------------------------------------------
# POST /refresh
# ---------------------------------------------------------------------------
@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Rotate the refresh session and issue a new access token",
    responses={401: {"description": "Missing/invalid/revoked refresh token."}},
)
async def refresh(
    request: Request,
    response: Response,
    settings: SettingsDep,
    repository: RepositoryDep,
) -> TokenPair:
    verify_stateful_request(request, settings)

    raw = request.cookies.get(settings.refresh_cookie_name)
    if not raw:
        raise AuthenticationError("Missing refresh token.")
    try:
        claims = decode_token(settings, raw, expected_type="refresh")
    except TokenError:
        _clear_session_cookies(response, settings)
        raise AuthenticationError("Invalid or expired refresh token.") from None

    # Re-check live membership before minting anything new.
    membership = await repository.get_membership(
        user_id=claims.subject, organization_id=claims.organization_id
    )
    if membership is None or not membership.is_active:
        if claims.family is not None:
            await repository.revoke_family(
                organization_id=claims.organization_id, family_id=claims.family
            )
        _clear_session_cookies(response, settings)
        raise AuthenticationError("Session is no longer valid.")

    # A well-formed refresh token always carries a family; a missing one means a
    # forged/corrupt token, so reject it rather than relying on an assert (which
    # python -O would strip).
    if claims.family is None:
        _clear_session_cookies(response, settings)
        raise AuthenticationError("Invalid or expired refresh token.")
    new_refresh, _jti, new_exp = issue_refresh_token(
        settings,
        user_id=claims.subject,
        organization_id=claims.organization_id,
        family=claims.family,
    )
    result = await repository.rotate_session(
        organization_id=claims.organization_id,
        presented_hash=token_digest(raw),
        new_hash=token_digest(new_refresh),
        new_expires_at=new_exp,
    )
    if result.status is not RotateStatus.ROTATED:
        # REUSED already revoked the family in-store; UNKNOWN means no such row.
        _clear_session_cookies(response, settings)
        raise AuthenticationError("Refresh token reuse detected or session revoked.")

    access_token, _access_exp = issue_access_token(
        settings,
        user_id=claims.subject,
        organization_id=claims.organization_id,
        role=membership.role,
    )
    csrf_token = secrets.token_urlsafe(32)
    _set_session_cookies(
        response,
        settings,
        refresh_token=new_refresh,
        csrf_token=csrf_token,
        max_age=settings.refresh_token_ttl_seconds,
    )
    return TokenPair(
        access_token=access_token,
        expires_in=settings.access_token_ttl_seconds,
        csrf_token=csrf_token,
    )


# ---------------------------------------------------------------------------
# POST /logout
# ---------------------------------------------------------------------------
@router.post(
    "/logout",
    response_model=StatusResponse,
    summary="Revoke the refresh family and clear session cookies",
)
async def logout(
    request: Request,
    response: Response,
    settings: SettingsDep,
    repository: RepositoryDep,
) -> StatusResponse:
    verify_stateful_request(request, settings)

    raw = request.cookies.get(settings.refresh_cookie_name)
    if raw:
        claims: TokenClaims | None
        try:
            claims = decode_token(settings, raw, expected_type="refresh")
        except TokenError:
            claims = None
        if claims is not None and claims.family is not None:
            await repository.revoke_family(
                organization_id=claims.organization_id, family_id=claims.family
            )
    _clear_session_cookies(response, settings)
    return StatusResponse(status="logged_out")


# ---------------------------------------------------------------------------
# GET /me
# ---------------------------------------------------------------------------
@router.get("/me", response_model=MeResponse, summary="Current authenticated user")
async def me(principal: PrincipalDep) -> MeResponse:
    return MeResponse(
        user_id=str(principal.user_id),
        organization_id=str(principal.organization_id),
        role=principal.role.value,
        permissions=sorted(p.value for p in permissions_for(principal.role)),
    )
