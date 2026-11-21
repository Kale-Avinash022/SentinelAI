"""Day 014 contract tests: the ``/api/v1/configs`` HTTP surface.

Full request/response contract — RBAC guards, the redacted read model, the
generic error envelope and the compare-and-swap status codes — against an
in-memory :class:`FakeConfigVersionStore` that implements the same
:class:`app.services.config_versions.ConfigVersionStore` protocol as the
Postgres store. No database is touched: the injectability proven on Day 012/013
is reused through ``app.state.config_version_store``.

What the HTTP layer alone can be blamed for is what this pins:

* the two write authorities are *separate* from the read authority, and a
  refused call never reaches the store;
* revealing a sealed body is its own privileged route, so the plaintext cannot
  ride along on an ordinary version read;
* a CAS conflict is a 409 ``activation_conflict`` envelope with ``retryable``
  set, telling the caller to re-read rather than to give up;
* the request models are closed, so an unexpected field is a 422 rather than a
  silently-dropped capability.

The database-backed facts (grants, RLS, partial unique index, CHECKs) are
proven in ``tests/security/test_config_versions.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from typing import Any

import httpx
import pytest
from app.auth.repository import LoginIdentity, Membership
from app.auth.security import hash_password
from app.configs.rules import content_hash, diff_payloads, fingerprint, parse_payload
from app.core.errors import AppError
from app.core.time import utcnow
from app.main import create_app
from app.services.applications import NotFoundError, Page
from app.services.config_versions import (
    ActivationConflictError,
    ActivationView,
    ConfigVersionView,
    InvalidConfigPayloadError,
    QualificationRequiredError,
    RevealedBody,
    VersionDiffView,
)
from pydantic import ValidationError

from tests.conftest import TEST_SECRET_KEY, make_settings

EMAIL = "author@example.test"
PASSWORD = "versions-correct-horse"
USER_ID = uuid.UUID("d0000000-0000-4000-8000-00000000000a")
ORG_ID = uuid.UUID("d1000000-0000-4000-8000-00000000000b")
ENVELOPE_KEYS = {"code", "message", "request_id", "retryable"}

BASE = "/api/v1/configs"
PROMPT_BODY = "You are a triage assistant for {{customer_name}}."
SECRET_REF = "secret://vault/triage-v1"

ROUTE_PAYLOAD: dict[str, Any] = {
    "primary_provider": "openai",
    "allowed_providers": ["openai", "anthropic"],
    "model_name": "gpt-4o-mini",
    "capabilities": {"streaming": True, "tools": False, "vision": False},
    "privacy_class": "internal",
    "quality_floor": 0.8,
    "timeout_ms": 5000,
    "max_input_tokens": 8000,
    "monthly_budget_usd": 500.0,
    "evaluator_version": "eval-v2",
}
PROMPT_PAYLOAD: dict[str, Any] = {
    "provider": "openai",
    "model_name": "gpt-4o-mini",
    "variables": ["customer_name"],
    "output_format": "text",
    "max_output_tokens": 800,
    "quality_floor": 0.7,
    "evaluator_version": "eval-v2",
}


class FakeConfigVersionStore:
    """In-memory :class:`ConfigVersionStore` mirroring the Postgres semantics:
    append-only ladders, one live activation per slot and CAS refusal."""

    def __init__(self) -> None:
        self.versions: dict[uuid.UUID, ConfigVersionView] = {}
        self.bodies: dict[uuid.UUID, str] = {}  # the "ciphertext", keyed by version
        self.activations: dict[uuid.UUID, ActivationView] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    # -- helpers -------------------------------------------------------------

    def _ladder(self, kind: str, name: str) -> list[ConfigVersionView]:
        return [
            view
            for view in self.versions.values()
            if view.organization_id == ORG_ID and view.kind == kind and view.name == name
        ]

    def _live(self, kind: str, name: str) -> ActivationView | None:
        return next(
            (
                view
                for view in self.activations.values()
                if view.organization_id == ORG_ID
                and view.kind == kind
                and view.name == name
                and view.is_active
            ),
            None,
        )

    @staticmethod
    def _page(items: Sequence[Any], limit: int) -> Page[Any]:
        ordered = sorted(items, key=lambda item: item.created_at, reverse=True)
        return Page(items=list(ordered[:limit]), next_cursor=None)

    # -- ConfigVersionStore surface ------------------------------------------

    async def create_version(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        kind: str,
        name: str,
        payload: Mapping[str, Any],
        body: str | None = None,
        secret_ref: str | None = None,
        request_id: str | None,
    ) -> ConfigVersionView:
        self.calls.append(("create_version", {"kind": kind, "name": name, "body": body}))
        # Mirror the store's mapping: a schema or pairing violation is a typed
        # 422, never a raw pydantic traceback (whose values may be secret).
        try:
            parsed = parse_payload(kind, payload)
        except ValidationError as exc:
            raise InvalidConfigPayloadError(
                "Configuration payload failed schema validation: "
                + "; ".join(
                    f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                    for error in exc.errors()
                )
            ) from exc
        metadata = parsed.model_dump(mode="json")
        ladder = self._ladder(kind, name)
        view = ConfigVersionView(
            id=uuid.uuid4(),
            organization_id=organization_id,
            kind=kind,
            name=name,
            version_number=len(ladder) + 1,
            content_hash=content_hash(kind, metadata, body=body, secret_ref=secret_ref),
            payload=metadata,
            body_storage="encrypted" if body else "reference" if secret_ref else "none",
            secret_ref_fingerprint=fingerprint(secret_ref) if secret_ref else None,
            created_by_user_id=actor_user_id,
            created_at=utcnow() + timedelta(milliseconds=len(ladder)),
        )
        self.versions[view.id] = view
        if body is not None:
            self.bodies[view.id] = body
        return view

    async def get_version(
        self, *, organization_id: uuid.UUID, version_id: uuid.UUID
    ) -> ConfigVersionView:
        view = self.versions.get(version_id)
        if view is None or view.organization_id != organization_id:
            raise NotFoundError("Configuration version not found.")
        return view

    async def list_versions(
        self, *, organization_id: uuid.UUID, kind: str, name: str, cursor: str | None, limit: int
    ) -> Page[ConfigVersionView]:
        return self._page(self._ladder(kind, name), limit)

    async def diff_versions(
        self,
        *,
        organization_id: uuid.UUID,
        before_version_id: uuid.UUID,
        after_version_id: uuid.UUID,
    ) -> VersionDiffView:
        before = await self.get_version(
            organization_id=organization_id, version_id=before_version_id
        )
        after = await self.get_version(organization_id=organization_id, version_id=after_version_id)
        entries = diff_payloads(before.payload, after.payload)
        hashes_differ = before.content_hash != after.content_hash
        return VersionDiffView(
            kind=before.kind,
            name=before.name,
            before_version_id=before.id,
            after_version_id=after.id,
            before_version_number=before.version_number,
            after_version_number=after.version_number,
            before_content_hash=before.content_hash,
            after_content_hash=after.content_hash,
            identical=not hashes_differ,
            entries=entries,
            body_change_unverified=hashes_differ and bool(entries),
        )

    async def reveal_body(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        version_id: uuid.UUID,
        request_id: str | None,
    ) -> RevealedBody:
        self.calls.append(("reveal_body", {"version_id": version_id}))
        view = await self.get_version(organization_id=organization_id, version_id=version_id)
        return RevealedBody(
            version_id=view.id,
            version_number=view.version_number,
            content_hash=view.content_hash,
            body=self.bodies[view.id],
        )

    async def activate(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        kind: str,
        name: str,
        version_id: uuid.UUID,
        expected_previous_number: int | None,
        qualification: Mapping[str, Any] | None,
        is_last_known_good: bool,
        request_id: str | None,
    ) -> ActivationView:
        self.calls.append(
            ("activate", {"kind": kind, "name": name, "expected": expected_previous_number})
        )
        view = await self.get_version(organization_id=organization_id, version_id=version_id)
        if is_last_known_good and not qualification:
            raise QualificationRequiredError(
                "A version may only be marked last-known-good with explicit qualification evidence."
            )
        live = self._live(kind, name)
        expected = 0 if expected_previous_number is None else expected_previous_number
        actual = live.version_number if live is not None else 0
        if expected != actual:
            raise ActivationConflictError(
                f"Expected previous version {expected}, but version {actual} is active."
            )
        if live is not None:
            self.activations[live.id] = replace(live, is_active=False, superseded_at=utcnow())
        activation = ActivationView(
            id=uuid.uuid4(),
            organization_id=organization_id,
            kind=kind,
            name=name,
            version_id=view.id,
            version_number=view.version_number,
            expected_previous_number=actual or None,
            supersedes_id=live.id if live is not None else None,
            activated_by_user_id=actor_user_id,
            activated_at=utcnow(),
            superseded_at=None,
            qualification=dict(qualification) if qualification else None,
            is_last_known_good=is_last_known_good,
            is_active=True,
        )
        self.activations[activation.id] = activation
        return activation

    async def get_active(
        self, *, organization_id: uuid.UUID, kind: str, name: str
    ) -> ActivationView:
        live = self._live(kind, name)
        if live is None:
            raise NotFoundError("No version is active for this configuration slot.")
        return live

    async def get_last_known_good(
        self, *, organization_id: uuid.UUID, kind: str, name: str
    ) -> ActivationView:
        qualified = [
            view
            for view in self.activations.values()
            if view.organization_id == organization_id
            and view.kind == kind
            and view.name == name
            and view.is_last_known_good
        ]
        if not qualified:
            raise NotFoundError("No last-known-good version qualifies for this slot.")
        return max(qualified, key=lambda view: view.activated_at)

    async def list_activations(
        self, *, organization_id: uuid.UUID, kind: str, name: str, cursor: str | None, limit: int
    ) -> Page[ActivationView]:
        rows = [
            view
            for view in self.activations.values()
            if view.kind == kind and view.name == name and view.organization_id == organization_id
        ]
        return Page(
            items=sorted(rows, key=lambda v: v.activated_at, reverse=True)[:limit], next_cursor=None
        )


@asynccontextmanager
async def api_client(
    store: FakeConfigVersionStore | None, *, role: str = "owner"
) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(
        environment="test",
        secret_key=TEST_SECRET_KEY,
        database_url="",
        redis_url="",
    )
    app = create_app(settings)
    app.state.auth_repository = FakeAuthRepository(role=role)
    app.state.config_version_store = store
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://svc"
    ) as client:
        yield client


class FakeAuthRepository:
    """Minimal identity store so the Day 012 dependency chain can resolve."""

    def __init__(self, *, role: str = "owner") -> None:
        self.identities = {
            EMAIL: LoginIdentity(
                user_id=USER_ID,
                organization_id=ORG_ID,
                password_hash=hash_password(PASSWORD),
                is_active=True,
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
        raise AssertionError("refresh is not part of the Day 014 contract")

    async def revoke_family(self, **kwargs: Any) -> None:
        pass


async def _login(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _create(
    client: httpx.AsyncClient,
    auth: dict[str, str],
    *,
    kind: str = "provider_route",
    name: str = "triage-route",
    payload: dict[str, Any] | None = None,
    **extra: Any,
) -> httpx.Response:
    body: dict[str, Any] = {
        "kind": kind,
        "name": name,
        "payload": dict(payload or ROUTE_PAYLOAD),
        **extra,
    }
    return await client.post(f"{BASE}/versions", json=body, headers=auth)


# ---------------------------------------------------------------------------
# Authentication, availability and RBAC guards (ADR-0004 matrix v1)
# ---------------------------------------------------------------------------


async def test_unauthenticated_requests_get_generic_envelope() -> None:
    async with api_client(FakeConfigVersionStore()) as client:
        response = await client.get(f"{BASE}/provider_route/triage/versions")
    assert response.status_code == 401
    body = response.json()
    assert ENVELOPE_KEYS == body.keys()
    assert body["code"] == "unauthenticated"
    assert body["retryable"] is False


async def test_missing_store_fails_closed_with_503() -> None:
    """A wiring or outage problem is never an unauthenticated free pass."""
    async with api_client(None) as client:
        auth = await _login(client)
        response = await client.get(f"{BASE}/provider_route/triage/versions", headers=auth)
    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert response.json()["retryable"] is True


async def test_viewer_may_read_history_but_never_write_it() -> None:
    store = FakeConfigVersionStore()
    async with api_client(store, role="viewer") as client:
        auth = await _login(client)
        created = await _create(client, auth)
        assert created.status_code == 403, created.text
        assert created.json()["code"] == "permission_denied"
        activation = await client.post(
            f"{BASE}/provider_route/triage-route/activations",
            json={"version_id": str(uuid.uuid4())},
            headers=auth,
        )
        assert activation.status_code == 403
        empty = await client.get(f"{BASE}/provider_route/triage-route/versions", headers=auth)
        assert empty.status_code == 200
    assert store.calls == []  # refused at the guard, before the store


async def test_operator_may_not_author_a_version_or_reveal_a_body() -> None:
    """``administer:provider-config`` is Owner/Admin only, and the privileged
    reveal route uses the same authority as authoring."""
    store = FakeConfigVersionStore()
    async with api_client(store, role="owner") as client:
        auth = await _login(client)
        made = await _create(
            client,
            auth,
            kind="prompt",
            name="triage-prompt",
            payload=PROMPT_PAYLOAD,
            body=PROMPT_BODY,
        )
        assert made.status_code == 201
        version_id = made.json()["id"]
    async with api_client(store, role="operator") as client:
        auth = await _login(client)
        reveal = await client.post(f"{BASE}/versions/{version_id}/reveal-body", headers=auth)
        assert reveal.status_code == 403
        propose = await _create(client, auth)
        assert propose.status_code == 403
        read = await client.get(f"{BASE}/versions/{version_id}", headers=auth)
        assert read.status_code == 200
    assert not [name for name, _ in store.calls if name == "reveal_body"]


async def test_admin_may_author_and_activate() -> None:
    """Matrix v1 grants both write verbs to Admin, so the happy path must work
    for a non-Owner operator of the tenant."""
    store = FakeConfigVersionStore()
    async with api_client(store, role="admin") as client:
        auth = await _login(client)
        made = await _create(client, auth)
        assert made.status_code == 201
        activated = await client.post(
            f"{BASE}/provider_route/triage-route/activations",
            json={"version_id": made.json()["id"], "expected_previous_version_number": None},
            headers=auth,
        )
        assert activated.status_code == 201, activated.text
        assert activated.json()["is_active"] is True


# ---------------------------------------------------------------------------
# Version creation contract
# ---------------------------------------------------------------------------


async def test_created_version_never_exposes_the_body_or_ciphertext() -> None:
    store = FakeConfigVersionStore()
    async with api_client(store) as client:
        auth = await _login(client)
        made = await _create(
            client,
            auth,
            kind="prompt",
            name="triage-prompt",
            payload=PROMPT_PAYLOAD,
            body=PROMPT_BODY,
        )
        assert made.status_code == 201
        body = made.json()
    assert body["version_number"] == 1
    assert body["body_storage"] == "encrypted"
    assert body["secret_ref_fingerprint"] is None
    assert set(body) == {
        "id",
        "organization_id",
        "kind",
        "name",
        "version_number",
        "content_hash",
        "payload",
        "body_storage",
        "secret_ref_fingerprint",
        "created_by_user_id",
        "created_at",
    }
    assert PROMPT_BODY not in str(body)
    assert "encrypted_payload" not in str(body)


async def test_ladder_numbers_are_assigned_by_the_store() -> None:
    store = FakeConfigVersionStore()
    async with api_client(store) as client:
        auth = await _login(client)
        first = await _create(client, auth)
        second = await _create(client, auth, payload={**ROUTE_PAYLOAD, "quality_floor": 0.9})
        ladder = await client.get(f"{BASE}/provider_route/triage-route/versions", headers=auth)
    assert first.json()["version_number"] == 1
    assert second.json()["version_number"] == 2
    assert [item["version_number"] for item in ladder.json()["items"]] == [2, 1]


async def test_unknown_field_in_payload_is_rejected_by_the_closed_schema() -> None:
    async with api_client(FakeConfigVersionStore()) as client:
        auth = await _login(client)
        response = await _create(client, auth, payload={**ROUTE_PAYLOAD, "api_key": "sk-live-x"})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_config_payload"


async def test_request_models_are_closed_to_extra_fields() -> None:
    async with api_client(FakeConfigVersionStore()) as client:
        auth = await _login(client)
        response = await client.post(
            f"{BASE}/versions",
            json={
                "kind": "provider_route",
                "name": "r",
                "payload": ROUTE_PAYLOAD,
                "api_key": "sk-live-should-never-echo",
            },
            headers=auth,
        )
    # The project's own handler renders request-validation failures in the same
    # generic envelope as everything else (plus a structural ``details`` list),
    # and it strips the ``input`` key — so a rejected secret is named by its
    # field path but never quoted back.
    assert response.status_code == 422
    body = response.json()
    assert ENVELOPE_KEYS < set(body)
    assert body["code"] == "validation_error"
    assert "sk-live-should-never-echo" not in response.text
    assert all("input" not in detail for detail in body["details"])


async def test_slot_name_pattern_is_enforced_at_the_edge() -> None:
    async with api_client(FakeConfigVersionStore()) as client:
        auth = await _login(client)
        response = await _create(client, auth, name="Triage Route")
    assert response.status_code == 422


async def test_missing_version_is_a_404_envelope() -> None:
    async with api_client(FakeConfigVersionStore()) as client:
        auth = await _login(client)
        response = await client.get(f"{BASE}/versions/{uuid.uuid4()}", headers=auth)
    assert response.status_code == 404
    body = response.json()
    assert ENVELOPE_KEYS == body.keys()
    assert body["code"] == "not_found"


# ---------------------------------------------------------------------------
# Diff contract: redaction over HTTP
# ---------------------------------------------------------------------------


async def test_diff_over_http_exposes_only_fingerprints() -> None:
    store = FakeConfigVersionStore()
    async with api_client(store) as client:
        auth = await _login(client)
        first = await _create(
            client,
            auth,
            kind="prompt",
            name="triage-prompt",
            payload=PROMPT_PAYLOAD,
            body=PROMPT_BODY,
        )
        second = await _create(
            client,
            auth,
            kind="prompt",
            name="triage-prompt",
            payload={**PROMPT_PAYLOAD, "quality_floor": 0.95},
            body="You are a terse triage assistant.",
        )
        diff = await client.get(
            f"{BASE}/prompt/triage-prompt/diff",
            params={"before": first.json()["id"], "after": second.json()["id"]},
            headers=auth,
        )
    assert diff.status_code == 200, diff.text
    payload = diff.json()
    assert payload["identical"] is False
    assert payload["body_change_unverified"] is True
    assert [entry["path"] for entry in payload["entries"]] == ["payload.quality_floor"]
    assert PROMPT_BODY not in diff.text
    assert "You are a terse" not in diff.text


# ---------------------------------------------------------------------------
# Activation contract: CAS, qualification, reveal
# ---------------------------------------------------------------------------


async def test_stale_activation_is_a_retryable_409_and_changes_nothing() -> None:
    store = FakeConfigVersionStore()
    async with api_client(store) as client:
        auth = await _login(client)
        first = await _create(client, auth)
        second = await _create(client, auth, payload={**ROUTE_PAYLOAD, "quality_floor": 0.9})
        ok = await client.post(
            f"{BASE}/provider_route/triage-route/activations",
            json={"version_id": first.json()["id"]},
            headers=auth,
        )
        assert ok.status_code == 201
        store.calls.clear()
        stale = await client.post(
            f"{BASE}/provider_route/triage-route/activations",
            json={
                "version_id": second.json()["id"],
                "expected_previous_version_number": 99,
            },
            headers=auth,
        )
        live = await client.get(f"{BASE}/provider_route/triage-route/activation", headers=auth)
    assert stale.status_code == 409
    body = stale.json()
    assert ENVELOPE_KEYS == body.keys()
    assert body["code"] == "activation_conflict"
    assert body["retryable"] is True
    assert live.json()["version_id"] == first.json()["id"]


async def test_last_known_good_requires_qualification_over_http() -> None:
    store = FakeConfigVersionStore()
    async with api_client(store) as client:
        auth = await _login(client)
        made = await _create(client, auth)
        claimed = await client.post(
            f"{BASE}/provider_route/triage-route/activations",
            json={"version_id": made.json()["id"], "is_last_known_good": True},
            headers=auth,
        )
        earned = await client.post(
            f"{BASE}/provider_route/triage-route/activations",
            json={
                "version_id": made.json()["id"],
                "is_last_known_good": True,
                "qualification": {"evaluation_run": "run-1", "score": 0.93},
            },
            headers=auth,
        )
        good = await client.get(f"{BASE}/provider_route/triage-route/last-known-good", headers=auth)
    assert claimed.status_code == 422
    assert claimed.json()["code"] == "qualification_required"
    assert earned.status_code == 201, earned.text
    assert earned.json()["is_last_known_good"] is True
    assert earned.json()["qualification"] == {"evaluation_run": "run-1", "score": 0.93}
    assert good.status_code == 200
    assert good.json()["version_id"] == made.json()["id"]


async def test_reveal_body_returns_plaintext_only_on_its_own_route() -> None:
    store = FakeConfigVersionStore()
    async with api_client(store) as client:
        auth = await _login(client)
        made = await _create(
            client,
            auth,
            kind="prompt",
            name="triage-prompt",
            payload=PROMPT_PAYLOAD,
            body=PROMPT_BODY,
        )
        version_id = made.json()["id"]
        plain_get = await client.get(f"{BASE}/versions/{version_id}", headers=auth)
        revealed = await client.post(f"{BASE}/versions/{version_id}/reveal-body", headers=auth)
    assert PROMPT_BODY not in plain_get.text
    assert revealed.status_code == 200
    assert revealed.json() == {
        "version_id": version_id,
        "version_number": 1,
        "content_hash": made.json()["content_hash"],
        "body": PROMPT_BODY,
    }


async def test_reference_backed_version_exposes_a_fingerprint_only() -> None:
    store = FakeConfigVersionStore()
    async with api_client(store) as client:
        auth = await _login(client)
        made = await _create(
            client,
            auth,
            kind="prompt",
            name="triage-prompt",
            payload=PROMPT_PAYLOAD,
            secret_ref=SECRET_REF,
        )
    body = made.json()
    assert body["body_storage"] == "reference"
    assert body["secret_ref_fingerprint"].startswith("sha256:")
    assert SECRET_REF not in str(body)


async def test_history_route_is_read_only_and_sees_every_activation() -> None:
    store = FakeConfigVersionStore()
    async with api_client(store) as client:
        auth = await _login(client)
        first = await _create(client, auth)
        second = await _create(client, auth, payload={**ROUTE_PAYLOAD, "quality_floor": 0.9})
        await client.post(
            f"{BASE}/provider_route/triage-route/activations",
            json={"version_id": first.json()["id"]},
            headers=auth,
        )
        await client.post(
            f"{BASE}/provider_route/triage-route/activations",
            json={"version_id": second.json()["id"], "expected_previous_version_number": 1},
            headers=auth,
        )
        history = await client.get(f"{BASE}/provider_route/triage-route/activations", headers=auth)
    numbers = [item["version_number"] for item in history.json()["items"]]
    assert numbers == [2, 1]
    assert [item["is_active"] for item in history.json()["items"]] == [True, False]


@pytest.mark.parametrize("verb", ["put", "patch", "delete"])
async def test_no_verb_can_rewrite_history(verb: str) -> None:
    """405 rather than 404/403: the routes do not exist, on top of the store
    exposing no update method and the database granting no write privilege."""
    async with api_client(FakeConfigVersionStore()) as client:
        auth = await _login(client)
        response = await client.request(
            verb.upper(), f"{BASE}/versions/{uuid.uuid4()}", headers=auth
        )
    assert response.status_code == 405, f"{verb}: {response.status_code} {response.text}"


async def test_app_error_subclasses_map_to_their_declared_status() -> None:
    """Guard the envelope contract the router relies on for each failure mode."""
    assert ActivationConflictError.status_code == 409
    assert ActivationConflictError.retryable is True
    assert QualificationRequiredError.status_code == 422
    assert issubclass(AppError, Exception)
