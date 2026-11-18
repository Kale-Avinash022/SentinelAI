"""Signed JWT access + refresh tokens (Day 012).

A tiny, deliberate boundary over PyJWT:

* **One pinned algorithm.** Verification passes ``algorithms=[settings.jwt_algorithm]``
  (HS256 only), so the ``alg``-confusion and ``alg:none`` downgrade attacks are
  structurally impossible — a token claiming any other algorithm fails.
* **Full claim validation.** Signature, ``exp`` (+ a small leeway for clock skew),
  ``iss`` and ``aud`` are all verified by :func:`decode_token`; a token minted by
  another service or for another audience is rejected even if correctly signed.
* **Type separation.** Access and refresh tokens carry a ``typ`` claim; decoding
  demands the expected type, so a refresh token can never be replayed as an
  access token (or vice-versa).

The signing key is derived from ``Settings.secret_key`` (a ``SecretStr``); it is
never a parameter that could accidentally default to a published test value.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from jwt import InvalidTokenError

from app.core.settings import Settings

TokenType = Literal["access", "refresh"]

# Tolerate up to this many seconds of clock skew between issuer and verifier.
_CLOCK_SKEW_SECONDS = 30


class TokenError(Exception):
    """Any JWT failure: bad signature, expiry, issuer, audience, type or shape.

    Intentionally one opaque type — the caller returns a single generic 401 and
    never learns *why* a token was rejected (no oracle on which check failed).
    """


def _now() -> datetime:
    return datetime.now(UTC)


def _signing_key(settings: Settings) -> str:
    return settings.secret_key.get_secret_value()


def _base_claims(
    settings: Settings,
    *,
    subject: uuid.UUID,
    organization_id: uuid.UUID,
    typ: TokenType,
    now: datetime,
    ttl_seconds: int,
) -> dict[str, Any]:
    return {
        "sub": str(subject),
        "org": str(organization_id),
        "typ": typ,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }


def issue_access_token(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    role: str,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Mint a short-lived access token. Returns ``(encoded_jwt, expires_at)``."""
    issued_at = now or _now()
    claims = _base_claims(
        settings,
        subject=user_id,
        organization_id=organization_id,
        typ="access",
        now=issued_at,
        ttl_seconds=settings.access_token_ttl_seconds,
    )
    claims["role"] = role
    token = jwt.encode(claims, _signing_key(settings), algorithm=settings.jwt_algorithm)
    expires_at = issued_at + timedelta(seconds=settings.access_token_ttl_seconds)
    return token, expires_at


def issue_refresh_token(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    family: uuid.UUID,
    now: datetime | None = None,
) -> tuple[str, str, datetime]:
    """Mint a refresh token bound to a rotation *family*.

    Returns ``(encoded_jwt, jti, expires_at)``. The caller stores only a
    hash of ``encoded_jwt`` (never the raw token) together with ``jti`` and
    ``family`` so reuse can be detected and the whole family revoked.
    """
    issued_at = now or _now()
    claims = _base_claims(
        settings,
        subject=user_id,
        organization_id=organization_id,
        typ="refresh",
        now=issued_at,
        ttl_seconds=settings.refresh_token_ttl_seconds,
    )
    claims["fam"] = str(family)
    token = jwt.encode(claims, _signing_key(settings), algorithm=settings.jwt_algorithm)
    expires_at = issued_at + timedelta(seconds=settings.refresh_token_ttl_seconds)
    return token, claims["jti"], expires_at


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Validated view of a decoded token — every field already type-checked."""

    subject: uuid.UUID
    organization_id: uuid.UUID
    token_type: TokenType
    jti: str
    issued_at: datetime
    expires_at: datetime
    role: str | None
    family: uuid.UUID | None


def _as_uuid(value: Any, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise TokenError(f"claim {field!r} is not a UUID") from exc


def _as_datetime(value: Any, field: str) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (ValueError, TypeError, OverflowError) as exc:
        raise TokenError(f"claim {field!r} is not a timestamp") from exc


def decode_token(settings: Settings, token: str, *, expected_type: TokenType) -> TokenClaims:
    """Verify a token's signature/claims and return its typed contents.

    Raises :class:`TokenError` for *any* problem. Verification pins the
    algorithm and requires ``exp``, ``iss`` and ``aud``; ``iat`` is required so
    a token with no issued-at time is refused.
    """
    try:
        payload = jwt.decode(
            token,
            _signing_key(settings),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            leeway=timedelta(seconds=_CLOCK_SKEW_SECONDS),
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except InvalidTokenError as exc:
        raise TokenError("token failed verification") from exc

    token_type = payload.get("typ")
    if token_type != expected_type:
        raise TokenError("token type mismatch")

    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti:
        raise TokenError("missing jti claim")

    family_raw = payload.get("fam")
    return TokenClaims(
        subject=_as_uuid(payload.get("sub"), "sub"),
        organization_id=_as_uuid(payload.get("org"), "org"),
        token_type=expected_type,
        jti=jti,
        issued_at=_as_datetime(payload.get("iat"), "iat"),
        expires_at=_as_datetime(payload.get("exp"), "exp"),
        role=payload.get("role") if expected_type == "access" else None,
        family=_as_uuid(family_raw, "fam") if family_raw is not None else None,
    )
