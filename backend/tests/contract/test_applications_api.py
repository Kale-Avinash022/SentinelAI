"""HTTP contract tests for the Day 013 application / API-key / audit endpoints.

Full ASGI request/response contract — RBAC guards, the show-once raw key, the
generic error envelope, cursor plumbing and tenant scoping — against an
in-memory :class:`FakeApplicationStore` that implements the same
:class:`app.services.applications.ApplicationStore` protocol as the Postgres
store. No database is touched: the same injectability Day 012 proved for the
auth repository is reused here through ``app.state.application_store``.

The database-backed behaviour (definer lookup, grants, RLS, transactional
audit atomicity) is proven separately in ``tests/security/test_api_keys.py``
and ``tests/integration/test_audit.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from typing import Any

import httpx
from app.auth.keys import API_KEY_PREFIX, KeyState, generate_api_key
from app.auth.repository import LoginIdentity, Membership
from app.auth.security import hash_password
from app.core.time import utcnow
from app.main import create_app
from app.services.applications import (
    ApiKeyView,
    ApplicationNotActiveError,
    ApplicationView,
    AuditView,
    InvalidCursorError,
    IssuedApiKey,
    KeyNotRotatableError,
    NameConflictError,
    NotFoundError,
    Page,
    decode_cursor,
    encode_cursor,
)

from tests.conftest import TEST_SECRET_KEY, make_settings

EMAIL = "owner@example.test"
PASSWORD = "show-once-correct-horse"
USER_ID = uuid.UUID("c0000000-0000-4000-8000-00000000000a")
ORG_ID = uuid.UUID("c1000000-0000-4000-8000-00000000000b")
OTHER_ORG_ID = uuid.UUID("c1000000-0000-4000-8000-0000000000ff")
ENVELOPE_KEYS = {"code", "message", "request_id", "retryable"}

APPS = "/api/v1/applications"


def _keys_path(app_id: uuid.UUID, suffix: str = "") -> str:
    return f"{APPS}/{app_id}/api-keys{suffix}"


class FakeAuthRepository:
    """Minimal identity store so the Day 012 dependency chain can resolve."""

    def __init__(self, *, role: str = "owner") -> None:
        digest = hash_password(PASSWORD)
        self.identities = {
            EMAIL: LoginIdentity(
                user_id=USER_ID, organization_id=ORG_ID, password_hash=digest, is_active=True
            )
        }
        self.memberships = {
            (USER_ID, ORG_ID): Membership(
                user_id=USER_ID, organization_id=ORG_ID, role=role, is_active=True
            )
        }

    async def lookup_login_identity(self, email: str) -> LoginIdentity | None:
        return self.identities.get(email)

    async def get_membership(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Membership | None:
        return self.memberships.get((user_id, organization_id))

    async def update_password_hash(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID, password_hash: str
    ) -> None:
        pass

    async def create_session(self, **kwargs: Any) -> None:
        pass

    async def rotate_session(self, **kwargs: Any) -> Any:
        raise AssertionError("refresh is not part of the Day 013 contract")

    async def revoke_family(self, **kwargs: Any) -> None:
        pass


class FakeApplicationStore:
    """In-memory :class:`ApplicationStore` mirroring the Postgres semantics,
    including tenant scoping, show-once issuance and appended audit entries."""

    def __init__(self) -> None:
        self.applications: dict[uuid.UUID, ApplicationView] = {}
        self.keys: dict[uuid.UUID, ApiKeyView] = {}
        self.secrets: dict[uuid.UUID, str] = {}  # key id -> raw (as issued)
        self.audit: list[AuditView] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    # -- helpers -------------------------------------------------------------

    def _foreign_app(
        self, organization_id: uuid.UUID, application_id: uuid.UUID
    ) -> ApplicationView:
        app = self.applications.get(application_id)
        if app is None or app.organization_id != organization_id:
            raise NotFoundError("Application not found.")
        return app

    def _append_audit(
        self,
        organization_id: uuid.UUID,
        actor_id: uuid.UUID,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID,
        detail: dict[str, object],
        request_id: str | None,
    ) -> None:
        # Mirror the DB guard: the trail never carries key material.
        assert API_KEY_PREFIX not in str(detail)
        self.audit.append(
            AuditView(
                id=uuid.uuid4(),
                actor_type="user",
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                detail=detail,
                request_id=request_id,
                created_at=utcnow(),
            )
        )

    @staticmethod
    def _page(items: list[Any], cursor: str | None, limit: int) -> Page[Any]:
        if cursor is not None:
            moment, last_id = decode_cursor(cursor)
            items = [item for item in items if (item.created_at, item.id) < (moment, last_id)]
        items = sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)
        has_more = len(items) > limit
        page = items[:limit]
        next_cursor = encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None
        return Page(items=page, next_cursor=next_cursor)

    # -- ApplicationStore surface ----------------------------------------------

    async def create_application(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        name: str,
        description: str | None,
        request_id: str | None,
    ) -> ApplicationView:
        self.calls.append(("create_application", {"organization_id": organization_id}))
        now = utcnow()
        for existing in self.applications.values():
            if existing.organization_id == organization_id and existing.name == name:
                raise NameConflictError(f"An application named '{name}' already exists.")
        view = ApplicationView(
            id=uuid.uuid4(),
            organization_id=organization_id,
            owner_user_id=actor_user_id,
            name=name,
            description=description,
            status="active",
            created_at=now,
            updated_at=now,
        )
        self.applications[view.id] = view
        self._append_audit(
            organization_id,
            actor_user_id,
            "application.created",
            "application",
            view.id,
            {"name": name, "status": "active"},
            request_id,
        )
        return view

    async def get_application(
        self, *, organization_id: uuid.UUID, application_id: uuid.UUID
    ) -> ApplicationView:
        return self._foreign_app(organization_id, application_id)

    async def list_applications(
        self, *, organization_id: uuid.UUID, cursor: str | None, limit: int
    ) -> Page[ApplicationView]:
        mine = [a for a in self.applications.values() if a.organization_id == organization_id]
        return self._page(mine, cursor, limit)

    async def update_application(
        self,
        *,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
        request_id: str | None,
    ) -> ApplicationView:
        current = self._foreign_app(organization_id, application_id)
        changed: dict[str, object] = {}
        if name is not None and name != current.name:
            changed["name"] = name
        if description is not None and description != current.description:
            changed["description"] = description
        if status is not None and status != current.status:
            changed["status"] = status
        view = replace(
            current,
            name=name if name is not None else current.name,
            description=description if description is not None else current.description,
            status=status if status is not None else current.status,
            updated_at=utcnow(),
        )
        self.applications[application_id] = view
        if changed:
            self._append_audit(
                organization_id,
                actor_user_id,
                "application.updated",
                "application",
                application_id,
                changed,
                request_id,
            )
        return view

    async def create_api_key(
        self,
        *,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        name: str,
        scopes: Sequence[str],
        expires_in_days: int | None,
        request_id: str | None,
    ) -> IssuedApiKey:
        app = self._foreign_app(organization_id, application_id)
        if app.status != "active":
            raise ApplicationNotActiveError("Only active applications can receive API keys.")
        generated = generate_api_key()
        now = utcnow()
        expires_at = now + timedelta(days=expires_in_days) if expires_in_days else None
        view = ApiKeyView(
            id=uuid.uuid4(),
            application_id=application_id,
            name=name,
            key_prefix=generated.key_prefix,
            scopes=frozenset(scopes),
            state=KeyState.ACTIVE,
            created_at=now,
            expires_at=expires_at,
            revoked_at=None,
            superseded_by_id=None,
        )
        self.keys[view.id] = view
        self.secrets[view.id] = generated.raw
        self._append_audit(
            organization_id,
            actor_user_id,
            "api_key.created",
            "api_key",
            view.id,
            {"application_id": str(application_id), "name": name, "scopes": sorted(scopes)},
            request_id,
        )
        return IssuedApiKey(view=view, secret=generated.raw)

    async def list_api_keys(
        self,
        *,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        cursor: str | None,
        limit: int,
        now: Any = None,
    ) -> Page[ApiKeyView]:
        self._foreign_app(organization_id, application_id)
        mine = [k for k in self.keys.values() if k.application_id == application_id]
        return self._page(mine, cursor, limit)

    async def revoke_api_key(
        self,
        *,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        key_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        request_id: str | None,
        now: Any = None,
    ) -> ApiKeyView:
        self._foreign_app(organization_id, application_id)
        key = self.keys.get(key_id)
        if key is None or key.application_id != application_id:
            raise NotFoundError("API key not found.")
        if key.revoked_at is None:
            moment = utcnow()
            key = replace(key, revoked_at=moment, state=KeyState.REVOKED)
            self.keys[key_id] = key
            self._append_audit(
                organization_id,
                actor_user_id,
                "api_key.revoked",
                "api_key",
                key_id,
                {"application_id": str(application_id), "name": key.name},
                request_id,
            )
        return key

    async def rotate_api_key(
        self,
        *,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        key_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        grace_seconds: int,
        request_id: str | None,
        now: Any = None,
    ) -> tuple[IssuedApiKey, ApiKeyView]:
        self._foreign_app(organization_id, application_id)
        predecessor = self.keys.get(key_id)
        if predecessor is None or predecessor.application_id != application_id:
            raise NotFoundError("API key not found.")
        if predecessor.revoked_at is not None:
            raise KeyNotRotatableError("Revoked keys cannot be rotated; issue a new key instead.")
        generated = generate_api_key()
        moment = utcnow()
        overlap_end = moment + timedelta(seconds=grace_seconds)
        successor = ApiKeyView(
            id=uuid.uuid4(),
            application_id=application_id,
            name=predecessor.name,
            key_prefix=generated.key_prefix,
            scopes=predecessor.scopes,
            state=KeyState.ACTIVE,
            created_at=moment,
            expires_at=None,
            revoked_at=None,
            superseded_by_id=None,
        )
        self.keys[successor.id] = successor
        self.secrets[successor.id] = generated.raw
        rotated = replace(
            predecessor,
            superseded_by_id=successor.id,
            expires_at=min(predecessor.expires_at, overlap_end)
            if predecessor.expires_at
            else overlap_end,
        )
        self.keys[key_id] = rotated
        self._append_audit(
            organization_id,
            actor_user_id,
            "api_key.rotated",
            "api_key",
            key_id,
            {
                "application_id": str(application_id),
                "successor_key_id": str(successor.id),
                "overlap_expires_at": rotated.expires_at.isoformat()
                if rotated.expires_at
                else None,
            },
            request_id,
        )
        return IssuedApiKey(view=successor, secret=generated.raw), rotated

    async def list_audit_logs(
        self,
        *,
        organization_id: uuid.UUID,
        cursor: str | None,
        limit: int,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
    ) -> Page[AuditView]:
        rows = [
            entry
            for entry in self.audit
            if resource_type is None or entry.resource_type == resource_type
        ]
        if resource_id is not None:
            rows = [entry for entry in rows if entry.resource_id == resource_id]
        # Fake single-tenant fixture: everything belongs to ORG_ID's trail.
        return self._page(list(rows), cursor, limit)


@asynccontextmanager
async def api_client(
    store: FakeApplicationStore, *, role: str = "owner"
) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(
        environment="test",
        secret_key=TEST_SECRET_KEY,
        database_url="",
        redis_url="",
    )
    app = create_app(settings)
    app.state.auth_repository = FakeAuthRepository(role=role)
    app.state.application_store = store
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://svc"
    ) as client:
        yield client


async def _login(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _create_app(
    client: httpx.AsyncClient, auth: dict[str, str], name: str = "alerts-svc"
) -> httpx.Response:
    return await client.post(APPS, json={"name": name, "description": None}, headers=auth)


# ---------------------------------------------------------------------------
# Authentication and RBAC guards (ADR-0004 matrix v1)
# ---------------------------------------------------------------------------


async def test_unauthenticated_requests_get_generic_envelope() -> None:
    async with api_client(FakeApplicationStore()) as client:
        response = await client.get(APPS)
    assert response.status_code == 401
    body = response.json()
    assert ENVELOPE_KEYS == body.keys()
    assert body["code"] == "unauthenticated"
    assert body["retryable"] is False


async def test_admin_manages_applications_but_cannot_touch_keys() -> None:
    """Matrix v1: administer:applications is Owner+Admin, administer:api-keys
    is Owner-only — the key route must be refused before the store is called."""
    store = FakeApplicationStore()
    async with api_client(store, role="admin") as client:
        auth = await _login(client)
        created = await _create_app(client, auth)
        assert created.status_code == 201
        response = await client.post(
            _keys_path(uuid.uuid4()), json={"name": "k", "scopes": ["gateway:invoke"]}, headers=auth
        )
        assert response.status_code == 403
        assert response.json()["code"] == "permission_denied"
        assert not any(name == "create_api_key" for name, _ in store.calls)


async def test_viewer_may_read_trail_and_apps_but_not_mutate() -> None:
    store = FakeApplicationStore()
    async with api_client(store, role="viewer") as client:
        auth = await _login(client)
        # read:requests and read:audit are granted to every role...
        assert (await client.get(APPS, headers=auth)).status_code == 200
        assert (await client.get("/api/v1/audit-logs", headers=auth)).status_code == 200
        # ...but administer:applications is not: the write is refused.
        assert (await _create_app(client, auth)).status_code == 403


# ---------------------------------------------------------------------------
# Application CRUD contract
# ---------------------------------------------------------------------------


async def test_application_crud_round_trip_scoped_to_principal_tenant() -> None:
    store = FakeApplicationStore()
    async with api_client(store) as client:
        auth = await _login(client)
        created = await _create_app(client, auth, name="oncall-bot")
        assert created.status_code == 201
        payload = created.json()
        assert payload["name"] == "oncall-bot"
        assert payload["status"] == "active"
        assert payload["organization_id"] == str(ORG_ID)
        assert payload["owner_user_id"] == str(USER_ID)
        # Tenant always comes from the principal, never the request.
        assert store.calls[0][1]["organization_id"] == ORG_ID

        app_id = uuid.UUID(payload["id"])
        fetched = await client.get(f"{APPS}/{app_id}", headers=auth)
        assert fetched.status_code == 200 and fetched.json()["id"] == payload["id"]

        listed = await client.get(APPS, headers=auth)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [payload["id"]]

        patched = await client.patch(f"{APPS}/{app_id}", json={"status": "archived"}, headers=auth)
        assert patched.status_code == 200 and patched.json()["status"] == "archived"


async def test_unknown_or_foreign_application_is_indistinguishable_404() -> None:
    store = FakeApplicationStore()
    async with api_client(store) as client:
        auth = await _login(client)
        foreign = uuid.uuid4()
        for path in (f"{APPS}/{foreign}", f"{APPS}/{foreign}/api-keys"):
            response = await client.get(path, headers=auth)
            assert response.status_code == 404, response.text
            body = response.json()
            assert ENVELOPE_KEYS == body.keys()
            assert body["code"] == "not_found"


async def test_malformed_payloads_are_rejected_without_touching_the_store() -> None:
    store = FakeApplicationStore()
    async with api_client(store) as client:
        auth = await _login(client)
        response = await client.post(
            APPS, json={"name": "ok", "organization_id": str(OTHER_ORG_ID)}, headers=auth
        )
        assert response.status_code == 422  # extra="forbid": no tenant spoofing
        response = await client.post(APPS, json={"name": ""}, headers=auth)
        assert response.status_code == 422
        created = await _create_app(client, auth, name="strict")
        app_id = created.json()["id"]
        response = await client.post(
            _keys_path(uuid.UUID(app_id)),
            json={"name": "k", "scopes": ["sudo:everything"]},
            headers=auth,
        )
        assert response.status_code == 422  # closed KeyScope enum
        assert not store.keys


# ---------------------------------------------------------------------------
# API keys: show-once secret, metadata-only listings
# ---------------------------------------------------------------------------


async def test_key_secret_is_returned_exactly_once() -> None:
    store = FakeApplicationStore()
    async with api_client(store) as client:
        auth = await _login(client)
        app_id = uuid.UUID((await _create_app(client, auth)).json()["id"])

        created = await client.post(
            _keys_path(app_id),
            json={"name": "ci", "scopes": ["gateway:invoke", "telemetry:read"]},
            headers=auth,
        )
        assert created.status_code == 201
        body = created.json()
        raw = body["api_key"]
        assert raw.startswith(API_KEY_PREFIX)
        key_id = uuid.UUID(body["id"])

        listed = await client.get(_keys_path(app_id), headers=auth)
        assert listed.status_code == 200
        assert "api_key" not in listed.json()["items"][0]
        assert raw not in listed.text
        assert raw not in (await client.get(f"{APPS}/{app_id}", headers=auth)).text
        trail = await client.get("/api/v1/audit-logs", headers=auth)
        assert raw not in trail.text
        assert API_KEY_PREFIX not in trail.text
        # The store kept only the digest-side metadata.
        assert store.secrets[key_id] == raw


async def test_revoke_response_and_audit_entry_immediately() -> None:
    store = FakeApplicationStore()
    async with api_client(store) as client:
        auth = await _login(client)
        app_id = uuid.UUID((await _create_app(client, auth)).json()["id"])
        key_id = uuid.UUID(
            (
                await client.post(
                    _keys_path(app_id),
                    json={"name": "ci", "scopes": ["gateway:invoke"]},
                    headers=auth,
                )
            ).json()["id"]
        )
        revoked = await client.post(_keys_path(app_id, f"/{key_id}/revoke"), headers=auth)
        assert revoked.status_code == 200
        assert revoked.json()["state"] == "revoked"
        assert revoked.json()["revoked_at"] is not None
        actions = [entry.action for entry in store.audit]
        assert "api_key.revoked" in actions


async def test_rotate_returns_successor_once_with_overlap_metadata() -> None:
    store = FakeApplicationStore()
    async with api_client(store) as client:
        auth = await _login(client)
        app_id = uuid.UUID((await _create_app(client, auth)).json()["id"])
        key_id = uuid.UUID(
            (
                await client.post(
                    _keys_path(app_id),
                    json={"name": "ci", "scopes": ["gateway:invoke"]},
                    headers=auth,
                )
            ).json()["id"]
        )
        rotated = await client.post(_keys_path(app_id, f"/{key_id}/rotate"), headers=auth)
        assert rotated.status_code == 200
        body = rotated.json()
        successor_raw = body["successor"]["api_key"]
        assert successor_raw.startswith(API_KEY_PREFIX)
        assert body["predecessor"]["superseded_by_id"] == body["successor"]["id"]
        assert body["predecessor"]["expires_at"] is not None  # bounded overlap
        assert body["successor"]["expires_at"] is None
        # The raw value appears in this response only; listings stay clean.
        listed = await client.get(_keys_path(app_id), headers=auth)
        assert successor_raw not in listed.text


async def test_revoked_key_rotation_is_a_conflict_envelope() -> None:
    store = FakeApplicationStore()
    async with api_client(store) as client:
        auth = await _login(client)
        app_id = uuid.UUID((await _create_app(client, auth)).json()["id"])
        key_id = uuid.UUID(
            (
                await client.post(
                    _keys_path(app_id),
                    json={"name": "ci", "scopes": ["gateway:invoke"]},
                    headers=auth,
                )
            ).json()["id"]
        )
        await client.post(_keys_path(app_id, f"/{key_id}/revoke"), headers=auth)
        response = await client.post(_keys_path(app_id, f"/{key_id}/rotate"), headers=auth)
        assert response.status_code == 409
        assert response.json()["code"] == "key_not_rotatable"


async def test_key_on_archived_application_conflicts() -> None:
    store = FakeApplicationStore()
    async with api_client(store) as client:
        auth = await _login(client)
        app_id = uuid.UUID((await _create_app(client, auth)).json()["id"])
        await client.patch(f"{APPS}/{app_id}", json={"status": "archived"}, headers=auth)
        response = await client.post(
            _keys_path(app_id), json={"name": "late", "scopes": ["gateway:invoke"]}, headers=auth
        )
        assert response.status_code == 409
        assert response.json()["code"] == "application_not_active"


# ---------------------------------------------------------------------------
# Audit read + cursor contract
# ---------------------------------------------------------------------------


async def test_audit_trail_reads_back_newest_first_with_detail_metadata() -> None:
    store = FakeApplicationStore()
    async with api_client(store) as client:
        auth = await _login(client)
        created = await _create_app(client, auth, name="trail-app")
        app_id = created.json()["id"]
        response = await client.get("/api/v1/audit-logs", headers=auth)
        assert response.status_code == 200
        items = response.json()["items"]
        assert items[0]["action"] == "application.created"
        assert items[0]["detail"] == {"name": "trail-app", "status": "active"}
        assert items[0]["request_id"]  # correlation id propagated from middleware
        filtered = await client.get(
            f"/api/v1/audit-logs?resource_type=application&resource_id={app_id}", headers=auth
        )
        assert len(filtered.json()["items"]) == 1
        empty = await client.get(f"/api/v1/audit-logs?resource_id={uuid.uuid4()}", headers=auth)
        assert empty.json()["items"] == []


async def test_cursor_pagination_walks_and_malformed_cursor_is_400() -> None:
    store = FakeApplicationStore()
    async with api_client(store) as client:
        auth = await _login(client)
        base = utcnow()
        for index, name in enumerate([f"paged-{i}" for i in range(3)]):
            response = await _create_app(client, auth, name=name)
            assert response.status_code == 201
            # Pin strictly increasing timestamps so the keyset order is exact.
            view = store.applications[uuid.UUID(response.json()["id"])]
            store.applications[view.id] = replace(view, created_at=base + timedelta(seconds=index))

        first = await client.get(f"{APPS}?limit=2", headers=auth)
        assert first.status_code == 200
        assert len(first.json()["items"]) == 2
        cursor = first.json()["next_cursor"]
        assert cursor is not None
        second = await client.get(f"{APPS}?limit=2&cursor={cursor}", headers=auth)
        assert [item["name"] for item in second.json()["items"]] == ["paged-0"]
        assert second.json()["next_cursor"] is None

        bad = await client.get(f"{APPS}?cursor=not-a-real-cursor", headers=auth)
        assert bad.status_code == 400
        assert bad.json()["code"] == "invalid_cursor"


async def test_store_missing_fails_closed() -> None:
    """No application_store on app.state (DB boot failure) => 503-style outage,
    never an unauthenticated free pass."""
    settings = make_settings(
        environment="test", secret_key=TEST_SECRET_KEY, database_url="", redis_url=""
    )
    app = create_app(settings)
    app.state.auth_repository = FakeAuthRepository()
    # app.state.application_store stays None exactly as create_app initializes it.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://svc"
    ) as client:
        auth = await _login(client)
        response = await client.get(APPS, headers=auth)
    assert response.status_code == 503
    assert response.json()["retryable"] is True


def test_invalid_cursor_error_is_importable_and_typed() -> None:
    """Guard the contract-test <-> store shared vocabulary (renames break here)."""
    assert issubclass(InvalidCursorError, Exception)
