"""Day 015 contract tests: the ``POST /api/v1/llm/chat`` HTTP surface.

Full request/response contract over an in-memory fake gateway — RBAC gate, the
normalized success shape, the standard error envelope for every provider
failure, and OpenAPI stability — with no database and no network. The provider
scenario is seeded by rebuilding the process gateway's registry (the same
injectability proven on Days 012–014 via ``app.state``), never by a field a
caller could forge.

What the HTTP layer alone owns, and therefore what this pins:

* authentication and the Viewer role exclusion happen before any provider runs;
* a success returns exactly the documented normalized fields, with ``usage.kind``
  and a ``request_id`` matching the correlation echoed in ``X-Request-ID``;
* each provider failure maps to its one standard status/code/retryable triple,
  with no vendor internals in the message;
* the request is closed and bounded, so extra keys, empty or oversized message
  lists, and unknown options are 422 rather than a silently-dropped capability;
* a missing gateway is a fail-closed 503, never a free pass.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from app.auth.repository import LoginIdentity, Membership
from app.auth.security import hash_password
from app.gateway.contracts import (
    FAKE_MODEL,
    FAKE_PROVIDER,
    CompletionRequest,
    ProviderCompletion,
)
from app.gateway.fakes import FakeScenario
from app.gateway.registry import ProviderRegistry, build_fake_registry
from app.gateway.service import ChatGateway
from app.main import create_app

from tests.conftest import TEST_SECRET_KEY, make_settings

EMAIL = "chat@example.test"
PASSWORD = "gateway-correct-horse"
USER_ID = uuid.UUID("e0000000-0000-4000-8000-00000000000a")
ORG_ID = uuid.UUID("e1000000-0000-4000-8000-00000000000b")
ENVELOPE_KEYS = {"code", "message", "request_id", "retryable"}

CHAT_PATH = "/api/v1/llm/chat"
BODY: dict[str, Any] = {"messages": [{"role": "user", "content": "hello sentinel"}]}


async def _noop(_seconds: float) -> None:
    return None


class FakeAuthRepository:
    """Minimal identity store so the Day 012 dependency chain resolves."""

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
        raise AssertionError("refresh is not part of the Day 015 contract")

    async def revoke_family(self, **kwargs: Any) -> None:
        pass


class _HangingProvider:
    """A provider that never answers, so the gateway deadline is what trips."""

    @property
    def name(self) -> str:
        return FAKE_PROVIDER

    @property
    def default_model(self) -> str:
        return FAKE_MODEL

    async def complete(self, request: CompletionRequest) -> ProviderCompletion:
        await asyncio.Future()  # blocks forever until the timeout cancels it
        raise AssertionError("unreachable")


def _gateway_for(scenario: FakeScenario) -> ChatGateway:
    registry = build_fake_registry(scenario, sleep=_noop)
    return ChatGateway(registry, default_timeout_ms=5_000, max_timeout_ms=30_000)


def _hanging_gateway() -> ChatGateway:
    registry = ProviderRegistry()
    registry.register(FAKE_PROVIDER, _HangingProvider(), make_default=True)
    return ChatGateway(registry, default_timeout_ms=60, max_timeout_ms=1_000)


@asynccontextmanager
async def api_client(
    gateway: ChatGateway | None, *, role: str = "owner"
) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(
        environment="test",
        secret_key=TEST_SECRET_KEY,
        database_url="",
        redis_url="",
    )
    app = create_app(settings)
    app.state.auth_repository = FakeAuthRepository(role=role)
    app.state.chat_gateway = gateway
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://svc"
    ) as client:
        yield client


async def _login(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ---------------------------------------------------------------------------
# Authentication and the role gate (ADR-0004; Viewer excluded)
# ---------------------------------------------------------------------------


async def test_unauthenticated_chat_gets_generic_envelope() -> None:
    async with api_client(_gateway_for(FakeScenario.SUCCESS)) as client:
        response = await client.post(CHAT_PATH, json=BODY)
    assert response.status_code == 401
    body = response.json()
    assert ENVELOPE_KEYS == body.keys()
    assert body["code"] == "unauthenticated"


@pytest.mark.parametrize("role", ["owner", "admin", "operator"])
async def test_non_viewer_roles_may_invoke_chat(role: str) -> None:
    async with api_client(_gateway_for(FakeScenario.SUCCESS), role=role) as client:
        auth = await _login(client)
        response = await client.post(CHAT_PATH, json=BODY, headers=auth)
    assert response.status_code == 200, response.text


async def test_viewer_is_forbidden_before_any_provider_runs() -> None:
    async with api_client(_gateway_for(FakeScenario.SUCCESS), role="viewer") as client:
        auth = await _login(client)
        response = await client.post(CHAT_PATH, json=BODY, headers=auth)
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


async def test_missing_gateway_fails_closed_with_503() -> None:
    """A wiring or outage problem is never an unauthenticated free pass."""
    async with api_client(None) as client:
        auth = await _login(client)
        response = await client.post(CHAT_PATH, json=BODY, headers=auth)
    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"


# ---------------------------------------------------------------------------
# Normalized success contract
# ---------------------------------------------------------------------------


async def test_success_returns_normalized_chat_completion() -> None:
    async with api_client(_gateway_for(FakeScenario.SUCCESS)) as client:
        auth = await _login(client)
        response = await client.post(CHAT_PATH, json=BODY, headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["provider"] == FAKE_PROVIDER
    assert body["model"] == FAKE_MODEL  # sentinel-auto resolved to the fake model
    assert isinstance(body["content"], str) and body["content"]
    assert body["finish_reason"] == "stop"
    assert body["usage"]["kind"] == "exact"
    assert (
        body["usage"]["total_tokens"]
        == body["usage"]["input_tokens"] + body["usage"]["output_tokens"]
    )
    assert isinstance(body["latency_ms"], int)


async def test_request_id_ties_response_to_correlation_header() -> None:
    async with api_client(_gateway_for(FakeScenario.SUCCESS)) as client:
        auth = await _login(client)
        response = await client.post(
            CHAT_PATH, json=BODY, headers={**auth, "X-Request-ID": "corr-123"}
        )
    assert response.headers["X-Request-ID"] == "corr-123"
    assert response.json()["request_id"] == "corr-123"


async def test_explicit_model_is_echoed_through() -> None:
    async with api_client(_gateway_for(FakeScenario.SUCCESS)) as client:
        auth = await _login(client)
        response = await client.post(
            CHAT_PATH, json={**BODY, "model": "custom-model-1"}, headers=auth
        )
    assert response.json()["model"] == "custom-model-1"


# ---------------------------------------------------------------------------
# Every provider failure maps to its one standard envelope
# ---------------------------------------------------------------------------

_SCENARIO_TO_ERROR: dict[FakeScenario, tuple[int, str, bool]] = {
    FakeScenario.RATE_LIMIT_429: (429, "rate_limited", True),
    FakeScenario.SERVER_ERROR_500: (500, "internal_error", False),
    FakeScenario.MALFORMED: (500, "internal_error", False),
    FakeScenario.EMPTY: (500, "internal_error", False),
    FakeScenario.RESET: (503, "provider_unavailable", True),
}


@pytest.mark.parametrize("scenario", list(_SCENARIO_TO_ERROR))
async def test_failure_scenario_maps_to_standard_envelope(scenario: FakeScenario) -> None:
    expected_status, expected_code, expected_retryable = _SCENARIO_TO_ERROR[scenario]
    async with api_client(_gateway_for(scenario)) as client:
        auth = await _login(client)
        response = await client.post(CHAT_PATH, json=BODY, headers=auth)
    assert response.status_code == expected_status
    body = response.json()
    assert ENVELOPE_KEYS == body.keys()
    assert body["code"] == expected_code
    assert body["retryable"] is expected_retryable
    # No provider internals or scenario naming leak into the message.
    assert "fake" not in body["message"].lower()


async def test_gateway_deadline_surfaces_provider_timeout() -> None:
    async with api_client(_hanging_gateway()) as client:
        auth = await _login(client)
        response = await client.post(CHAT_PATH, json=BODY, headers=auth)
    assert response.status_code == 504
    body = response.json()
    assert body["code"] == "provider_timeout"
    assert body["retryable"] is True


# ---------------------------------------------------------------------------
# The request is closed and bounded
# ---------------------------------------------------------------------------


async def test_extra_top_level_field_is_rejected() -> None:
    async with api_client(_gateway_for(FakeScenario.SUCCESS)) as client:
        auth = await _login(client)
        response = await client.post(CHAT_PATH, json={**BODY, "provider": "evil"}, headers=auth)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_empty_messages_is_rejected() -> None:
    async with api_client(_gateway_for(FakeScenario.SUCCESS)) as client:
        auth = await _login(client)
        response = await client.post(CHAT_PATH, json={"messages": []}, headers=auth)
    assert response.status_code == 422


async def test_unknown_option_key_is_rejected() -> None:
    async with api_client(_gateway_for(FakeScenario.SUCCESS)) as client:
        auth = await _login(client)
        response = await client.post(
            CHAT_PATH,
            json={**BODY, "options": {"temperature": 0.5, "jailbreak": True}},
            headers=auth,
        )
    assert response.status_code == 422


async def test_message_count_bound_is_enforced() -> None:
    too_many = [{"role": "user", "content": "x"} for _ in range(33)]
    async with api_client(_gateway_for(FakeScenario.SUCCESS)) as client:
        auth = await _login(client)
        response = await client.post(CHAT_PATH, json={"messages": too_many}, headers=auth)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# OpenAPI schema stability for the later adapters and clients
# ---------------------------------------------------------------------------


async def test_openapi_exposes_chat_operation_behind_bearer() -> None:
    settings = make_settings(
        environment="test", secret_key=TEST_SECRET_KEY, database_url="", redis_url=""
    )
    app = create_app(settings)
    schema = app.openapi()
    operation = schema["paths"][CHAT_PATH]["post"]
    assert any("HTTPBearer" in scheme for scheme in operation.get("security", []))
    success = operation["responses"]["200"]
    # The 200 schema is the normalized ChatResponse, not a vendor dictionary.
    ref_name = success["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    component = schema["components"]["schemas"][ref_name]
    assert set(component["properties"]) == {
        "object",
        "provider",
        "model",
        "content",
        "finish_reason",
        "usage",
        "latency_ms",
        "request_id",
    }
