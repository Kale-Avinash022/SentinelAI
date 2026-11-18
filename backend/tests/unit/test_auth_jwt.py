"""Unit tests for the pinned-algorithm JWT boundary (:mod:`app.auth.jwt`).

No I/O: these exercise signing/verification invariants directly — access vs.
refresh type separation, expiry, issuer/audience enforcement, tamper rejection,
and that verification pins the algorithm so an ``alg`` downgrade is impossible.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from app.auth.jwt import (
    TokenError,
    decode_token,
    issue_access_token,
    issue_refresh_token,
)
from app.core.settings import Settings

SECRET = "unit-test-secret-key-0123456789-0123456789"
USER = uuid.UUID("11111111-1111-4111-8111-111111111111")
ORG = uuid.UUID("22222222-2222-4222-8222-222222222222")
FAMILY = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "environment": "test",
        "secret_key": SECRET,
        "database_url": "",
        "redis_url": "",
        "access_token_ttl_seconds": 900,
        "refresh_token_ttl_seconds": 3600,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_access_token_roundtrip_carries_role() -> None:
    settings = _settings()
    token, expires_at = issue_access_token(
        settings, user_id=USER, organization_id=ORG, role="admin"
    )
    claims = decode_token(settings, token, expected_type="access")
    assert claims.subject == USER
    assert claims.organization_id == ORG
    assert claims.token_type == "access"
    assert claims.role == "admin"
    assert claims.family is None
    assert expires_at > datetime.now(UTC)


def test_refresh_token_roundtrip_carries_family_and_no_role() -> None:
    settings = _settings()
    token, jti, _exp = issue_refresh_token(
        settings, user_id=USER, organization_id=ORG, family=FAMILY
    )
    assert jti
    claims = decode_token(settings, token, expected_type="refresh")
    assert claims.family == FAMILY
    # Refresh tokens must not smuggle a role privilege.
    assert claims.role is None


def test_access_and_refresh_are_not_interchangeable() -> None:
    settings = _settings()
    access, _ = issue_access_token(settings, user_id=USER, organization_id=ORG, role="owner")
    refresh, _, _ = issue_refresh_token(settings, user_id=USER, organization_id=ORG, family=FAMILY)
    # A refresh token cannot be presented as an access token, nor vice-versa.
    with pytest.raises(TokenError):
        decode_token(settings, refresh, expected_type="access")
    with pytest.raises(TokenError):
        decode_token(settings, access, expected_type="refresh")


def test_expired_token_is_rejected() -> None:
    settings = _settings()
    past = datetime.now(UTC) - timedelta(seconds=settings.access_token_ttl_seconds + 120)
    token, _ = issue_access_token(
        settings, user_id=USER, organization_id=ORG, role="admin", now=past
    )
    with pytest.raises(TokenError):
        decode_token(settings, token, expected_type="access")


def test_foreign_signing_key_is_rejected() -> None:
    settings = _settings()
    token, _ = issue_access_token(settings, user_id=USER, organization_id=ORG, role="admin")
    other = _settings(secret_key="a-completely-different-secret-key-value-xyz")
    with pytest.raises(TokenError):
        decode_token(other, token, expected_type="access")


def test_wrong_issuer_and_audience_are_rejected() -> None:
    settings = _settings()
    now = datetime.now(UTC)
    base_claims = {
        "sub": str(USER),
        "org": str(ORG),
        "typ": "access",
        "jti": "x",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=300)).timestamp()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "role": "admin",
    }
    wrong_iss = jwt.encode({**base_claims, "iss": "evil"}, SECRET, algorithm=settings.jwt_algorithm)
    wrong_aud = jwt.encode(
        {**base_claims, "aud": "other"}, SECRET, algorithm=settings.jwt_algorithm
    )
    with pytest.raises(TokenError):
        decode_token(settings, wrong_iss, expected_type="access")
    with pytest.raises(TokenError):
        decode_token(settings, wrong_aud, expected_type="access")


def test_algorithm_none_downgrade_is_rejected() -> None:
    settings = _settings()
    now = datetime.now(UTC)
    # Forged token asserting no algorithm; pinned [HS256] verification must refuse.
    forged = jwt.encode(
        {
            "sub": str(USER),
            "org": str(ORG),
            "typ": "access",
            "jti": "x",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=300)).timestamp()),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "role": "owner",
        },
        None,  # type: ignore[arg-type]  # PyJWT requires a None key for the "none" alg
        algorithm="none",
    )
    with pytest.raises(TokenError):
        decode_token(settings, forged, expected_type="access")
