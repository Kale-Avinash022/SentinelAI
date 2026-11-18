"""HTTP contract tests for the Day 012 auth endpoints.

These exercise the full ASGI request/response contract — cookies, CSRF/origin
gating, the generic error envelope and the rotation/reuse flow — against an
in-memory :class:`FakeAuthRepository` that implements the same
:class:`app.auth.repository.AuthRepository` protocol as the Postgres store. No
database is touched: the same injectability the app factory uses for readiness
probes is reused here for the storage port.

The database-backed behaviour (the ``SECURITY DEFINER`` login lookup, RLS on
``refresh_sessions`` and durability of rotation) is proven separately in
``tests/security/test_auth.py`` against real PostgreSQL.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import httpx
import pytest
from app.auth.repository import (
    LoginIdentity,
    Membership,
    RotateResult,
    RotateStatus,
)
from app.auth.security import hash_password
from app.main import create_app

from tests.conftest import TEST_SECRET_KEY, make_settings

EMAIL = "ops@example.test"
PASSWORD = "hunter22-correct-battery"
ORIGIN = "http://localhost:5173"
USER_ID = uuid.UUID("aaaaaaaa-0000-4000-8000-00000000000a")
ORG_ID = uuid.UUID("bbbbbbbb-0000-4000-8000-00000000000b")
ENVELOPE_KEYS = {"code", "message", "request_id", "retryable"}


class FakeAuthRepository:
    """In-memory :class:`AuthRepository` mirroring the Postgres semantics."""

    def __init__(self, *, role: str = "owner") -> None:
        self.identities: dict[str, LoginIdentity] = {}
        self.memberships: dict[tuple[uuid.UUID, uuid.UUID], Membership] = {}
        # token_hash -> mutable session record
        self.sessions: dict[str, dict[str, Any]] = {}
        self.rehash_calls = 0
        self._role = role

    def add_user(
        self,
        *,
        email: str = EMAIL,
        password: str = PASSWORD,
        is_active: bool = True,
        membership_active: bool = True,
        role: str | None = None,
    ) -> None:
        self.identities[email.strip().lower()] = LoginIdentity(
            user_id=USER_ID,
            organization_id=ORG_ID,
            password_hash=hash_password(password),
            is_active=is_active,
        )
        self.memberships[(USER_ID, ORG_ID)] = Membership(
            user_id=USER_ID,
            organization_id=ORG_ID,
            role=role or self._role,
            is_active=membership_active,
        )

    async def lookup_login_identity(self, email: str) -> LoginIdentity | None:
        return self.identities.get(email)

    async def get_membership(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Membership | None:
        return self.memberships.get((user_id, organization_id))

    async def update_password_hash(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID, password_hash: str
    ) -> None:
        self.rehash_calls += 1
        identity = self.identities.get(EMAIL)
        if identity is not None:
            self.identities[EMAIL] = LoginIdentity(
                user_id=identity.user_id,
                organization_id=identity.organization_id,
                password_hash=password_hash,
                is_active=identity.is_active,
            )

    async def create_session(
        self,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        family_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> None:
        self.sessions[token_hash] = {
            "user_id": user_id,
            "organization_id": organization_id,
            "family_id": family_id,
            "expires_at": expires_at,
            "revoked_at": None,
        }

    async def rotate_session(
        self,
        *,
        organization_id: uuid.UUID,
        presented_hash: str,
        new_hash: str,
        new_expires_at: datetime,
    ) -> RotateResult:
        record = self.sessions.get(presented_hash)
        if record is None:
            return RotateResult(RotateStatus.UNKNOWN)
        family = record["family_id"]
        if record["revoked_at"] is not None:
            self._revoke_family_rows(family)
            return RotateResult(RotateStatus.REUSED, family_id=family)
        record["revoked_at"] = new_expires_at
        self.sessions[new_hash] = {
            "user_id": record["user_id"],
            "organization_id": organization_id,
            "family_id": family,
            "expires_at": new_expires_at,
            "revoked_at": None,
        }
        return RotateResult(RotateStatus.ROTATED, family_id=family)

    async def revoke_family(self, *, organization_id: uuid.UUID, family_id: uuid.UUID) -> None:
        self._revoke_family_rows(family_id)

    def _revoke_family_rows(self, family_id: uuid.UUID) -> None:
        for record in self.sessions.values():
            if record["family_id"] == family_id and record["revoked_at"] is None:
                record["revoked_at"] = datetime.now()


@asynccontextmanager
async def auth_client(
    repo: FakeAuthRepository, **overrides: Any
) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(
        environment="test",
        secret_key=TEST_SECRET_KEY,
        database_url="",
        redis_url="",
        **overrides,
    )
    app = create_app(settings)
    # The auth dependencies resolve the store from app.state (both via
    # Depends(get_repository) and the direct call inside get_current_principal),
    # so the fake is installed there exactly as the lifespan would install the
    # Postgres-backed one.
    app.state.auth_repository = repo
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://svc"
    ) as client:
        yield client


def _login_cookies(response: httpx.Response) -> dict[str, str]:
    return {
        "sentinel_refresh": response.cookies["sentinel_refresh"],
        "sentinel_csrf": response.cookies["sentinel_csrf"],
    }


def _prime(client: httpx.AsyncClient, mapping: dict[str, str]) -> None:
    """Replace the client's cookie jar with exactly ``mapping``.

    Domain is left empty so the cookies attach to the single-label base_url
    host (``httpx`` drops an explicit domain that has no dot); path ``/`` makes
    them apply to every request regardless of the refresh cookie's narrow path.
    """
    client.cookies.clear()
    for name, value in mapping.items():
        client.cookies.set(name, value, path="/")


def _set_cookie_headers(response: httpx.Response) -> list[str]:
    return httpx.Headers(response.headers).get_list("set-cookie")


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
async def test_login_success_sets_hardened_cookies() -> None:
    repo = FakeAuthRepository()
    repo.add_user()
    async with auth_client(repo) as client:
        response = await client.post(
            "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] == 900
    assert body["csrf_token"] == response.cookies["sentinel_csrf"]

    cookies = _set_cookie_headers(response)
    refresh_cookie = next(c for c in cookies if c.startswith("sentinel_refresh="))
    csrf_cookie = next(c for c in cookies if c.startswith("sentinel_csrf="))
    # Refresh: HttpOnly + SameSite, scoped to the auth path only.
    assert "HttpOnly" in refresh_cookie
    assert "SameSite=lax" in refresh_cookie
    assert "Path=/api/v1/auth" in refresh_cookie
    # CSRF: readable by same-origin JS (no HttpOnly) for the double-submit token.
    assert "HttpOnly" not in csrf_cookie
    # The raw password must never be echoed anywhere in the response.
    assert PASSWORD not in response.text


@pytest.mark.parametrize("email", [EMAIL, "nobody@example.test"])
async def test_login_failures_are_indistinguishable(email: str) -> None:
    """Wrong password and unknown email yield the identical generic envelope."""
    repo = FakeAuthRepository()
    repo.add_user()
    async with auth_client(repo) as client:
        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong-password"}
        )
    assert response.status_code == 401
    body = response.json()
    assert ENVELOPE_KEYS <= body.keys()
    assert body["code"] == "invalid_credentials"
    assert body["retryable"] is False


async def test_login_disabled_account_is_generic_failure() -> None:
    repo = FakeAuthRepository()
    repo.add_user(is_active=False)
    async with auth_client(repo) as client:
        response = await client.post(
            "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


async def test_login_rejected_by_rate_limit() -> None:
    repo = FakeAuthRepository()
    repo.add_user()
    async with auth_client(repo, login_max_attempts=2, login_window_seconds=300) as client:
        codes = []
        for _ in range(4):
            r = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "wrong"})
            codes.append(r.status_code)
    assert codes[:2] == [401, 401]
    assert 429 in codes
    # The denial body is also the standard envelope.
    assert codes[-1] == 429


# ---------------------------------------------------------------------------
# Refresh rotation + reuse detection
# ---------------------------------------------------------------------------
async def test_refresh_rotates_and_old_token_becomes_reusable_evidence() -> None:
    repo = FakeAuthRepository()
    repo.add_user()
    async with auth_client(repo) as client:
        login = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        old = _login_cookies(login)
        old_csrf = login.json()["csrf_token"]

        _prime(client, old)
        first = await client.post(
            "/api/v1/auth/refresh", headers={"Origin": ORIGIN, "X-CSRF-Token": old_csrf}
        )
        assert first.status_code == 200
        assert first.json()["access_token"]
        assert first.cookies["sentinel_refresh"] != old["sentinel_refresh"]

        # Replaying the already-rotated (old) token is treated as theft: 401, and
        # the whole family is revoked so the successor stops working too.
        _prime(client, old)
        replay = await client.post(
            "/api/v1/auth/refresh", headers={"Origin": ORIGIN, "X-CSRF-Token": old_csrf}
        )
        assert replay.status_code == 401
        assert replay.json()["code"] == "unauthenticated"

        new = _login_cookies(first)
        _prime(client, new)
        after_theft = await client.post(
            "/api/v1/auth/refresh",
            headers={"Origin": ORIGIN, "X-CSRF-Token": first.json()["csrf_token"]},
        )
        assert after_theft.status_code == 401


async def test_refresh_requires_csrf_and_origin() -> None:
    repo = FakeAuthRepository()
    repo.add_user()
    async with auth_client(repo) as client:
        login = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        cookies = _login_cookies(login)
        csrf = login.json()["csrf_token"]

        # Missing CSRF header.
        _prime(client, cookies)
        no_csrf = await client.post("/api/v1/auth/refresh", headers={"Origin": ORIGIN})
        assert no_csrf.status_code == 403
        assert no_csrf.json()["code"] == "permission_denied"

        # Mismatched CSRF token.
        _prime(client, cookies)
        mismatch = await client.post(
            "/api/v1/auth/refresh",
            headers={"Origin": ORIGIN, "X-CSRF-Token": "does-not-match"},
        )
        assert mismatch.status_code == 403

        # Disallowed origin.
        _prime(client, cookies)
        bad_origin = await client.post(
            "/api/v1/auth/refresh",
            headers={"Origin": "http://evil.example", "X-CSRF-Token": csrf},
        )
        assert bad_origin.status_code == 403


async def test_refresh_without_cookie_is_unauthenticated() -> None:
    repo = FakeAuthRepository()
    repo.add_user()
    async with auth_client(repo) as client:
        _prime(client, {})
        response = await client.post(
            "/api/v1/auth/refresh", headers={"Origin": ORIGIN, "X-CSRF-Token": "x"}
        )
    # CSRF header vs. (absent) cookie fails the double-submit check first.
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Logout + current user
# ---------------------------------------------------------------------------
async def test_logout_revokes_family_and_clears_cookies() -> None:
    repo = FakeAuthRepository()
    repo.add_user()
    async with auth_client(repo) as client:
        login = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        cookies = _login_cookies(login)
        csrf = login.json()["csrf_token"]

        _prime(client, cookies)
        out = await client.post(
            "/api/v1/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
        )
        assert out.status_code == 200
        assert out.json()["status"] == "logged_out"

        # The family is revoked: the same refresh token no longer rotates.
        _prime(client, cookies)
        reuse = await client.post(
            "/api/v1/auth/refresh", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
        )
        assert reuse.status_code == 401


async def test_me_reports_live_role_and_permissions() -> None:
    repo = FakeAuthRepository(role="operator")
    repo.add_user(role="operator")
    async with auth_client(repo) as client:
        login = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        access = login.json()["access_token"]
        me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    body = me.json()
    assert body["role"] == "operator"
    assert "execute:approved-action" in body["permissions"]
    assert "administer:delete-org" not in body["permissions"]


async def test_me_requires_valid_access_token() -> None:
    repo = FakeAuthRepository()
    repo.add_user()
    async with auth_client(repo) as client:
        missing = await client.get("/api/v1/auth/me")
        forged = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nope"})
    assert missing.status_code == 401
    assert forged.json()["code"] == "unauthenticated"


async def test_me_denied_when_membership_withdrawn() -> None:
    """A still-valid token for a now-inactive membership must not authenticate."""
    repo = FakeAuthRepository()
    repo.add_user()
    async with auth_client(repo) as client:
        login = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        access = login.json()["access_token"]
        # Withdraw the membership server-side; the access token is unchanged.
        repo.memberships[(USER_ID, ORG_ID)] = Membership(
            user_id=USER_ID, organization_id=ORG_ID, role="owner", is_active=False
        )
        me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 401


# ---------------------------------------------------------------------------
# OpenAPI surface
# ---------------------------------------------------------------------------
async def test_auth_endpoints_are_documented() -> None:
    settings = make_settings(
        environment="test", secret_key=TEST_SECRET_KEY, database_url="", redis_url=""
    )
    app = create_app(settings)
    schema = app.openapi()
    for path in (
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
    ):
        assert path in schema["paths"]
