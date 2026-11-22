"""Day 016 contract tests: the LiteLLM adapter behind ``/api/v1/llm/chat``.

The Day 015 HTTP surface must not change shape when ``sentinel-auto`` resolves
to a real vendor instead of a fake — this suite proves exactly that by
swapping only the *gateway build* (settings-selected provider through the
Day 016 gate, SDK replaced by the injected stub), leaving auth, RBAC, schemas
and the error envelope untouched. Default CI makes no external call: every
"vendor" response here comes from a stubbed SDK seam.

Pinned behaviours:

* a gated real-provider build answers 200 with ``provider`` naming the vendor
  and ``usage.kind`` from the vendor's counts;
* vendor authentication failure surfaces as the standard 401 envelope with
  none of the vendor's message text;
* an over-cap plan is refused before the SDK seam (no call recorded), so a
  caller can never spend past the configured cap through the API;
* the request still cannot choose providers, endpoints or keys — a pinned
  non-allowlisted model is a typed FORBIDDEN, not a proxy request.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
from app.auth.repository import LoginIdentity, Membership
from app.auth.security import hash_password
from app.gateway.adapters.litellm_adapter import LiteLLMAdapter
from app.gateway.allowlist import build_allowlist
from app.gateway.contracts import AUTO_MODEL
from app.gateway.registry import build_registry
from app.gateway.service import ChatGateway
from app.main import create_app
from pydantic import SecretStr

from tests.conftest import TEST_SECRET_KEY, make_settings

EMAIL = "adapter@example.test"
PASSWORD = "adapter-correct-horse"
USER_ID = uuid.UUID("e2000000-0000-4000-8000-00000000000c")
ORG_ID = uuid.UUID("e3000000-0000-4000-8000-00000000000d")
ENVELOPE_KEYS = {"code", "message", "request_id", "retryable"}

CHAT_PATH = "/api/v1/llm/chat"
BODY: dict[str, Any] = {"messages": [{"role": "user", "content": "hello sentinel"}]}


class FakeAuthRepository:
    """Same minimal identity store as the Day 015 HTTP contract suite."""

    def __init__(self, *, role: str = "operator") -> None:
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
        raise AssertionError("refresh is not part of the chat contract")

    async def revoke_family(self, **kwargs: Any) -> None:
        pass


class StubSDK:
    """Injected ``litellm``-shaped seam: records calls, answers or raises."""

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def acompletion(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="answer from vendor land"),
                    finish_reason="stop",
                )
            ],
            model="gpt-4o-mini-2024-07-18",
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )


class _Resolver:
    def resolve(self, reference: str) -> SecretStr:
        return SecretStr(f"resolved-{reference}")


def _gateway(
    sdk: StubSDK,
    *,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    spend_cap_usd: float = 0.05,
) -> ChatGateway:
    """Build through the *production shape* of the Day 016 gate: settings →
    build_registry → ChatGateway. Only the SDK and credential resolver are
    injected seams, so no environment key or network is ever consulted."""
    settings = make_settings(
        environment="test",
        secret_key=TEST_SECRET_KEY,
        database_url="",
        redis_url="",
        llm_default_provider=provider,
        llm_openai_model=model,
        llm_spend_cap_usd=spend_cap_usd,
    )
    registry = build_registry(settings, credential_resolver=_Resolver(), adapter_client=sdk)
    return ChatGateway(registry, default_timeout_ms=5_000, max_timeout_ms=30_000)


@asynccontextmanager
async def api_client(gateway: ChatGateway) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(
        environment="test",
        secret_key=TEST_SECRET_KEY,
        database_url="",
        redis_url="",
    )
    app = create_app(settings)
    app.state.auth_repository = FakeAuthRepository()
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
# The gated build answers through the unchanged HTTP contract
# ---------------------------------------------------------------------------


async def test_gated_openai_build_answers_normalized_success() -> None:
    sdk = StubSDK()
    async with api_client(_gateway(sdk)) as client:
        auth = await _login(client)
        response = await client.post(CHAT_PATH, json=BODY, headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-4o-mini-2024-07-18"  # vendor deployment identity
    assert body["content"] == "answer from vendor land"
    assert body["usage"]["kind"] == "exact"
    assert body["usage"]["total_tokens"] == 15
    # One SDK call, addressed LiteLLM-style, with the wire body translated.
    assert len(sdk.calls) == 1
    assert sdk.calls[0]["model"] == "openai/gpt-4o-mini"
    assert sdk.calls[0]["messages"] == [{"role": "user", "content": "hello sentinel"}]


async def test_vendor_auth_failure_maps_to_standard_401_envelope() -> None:
    auth_error = type("AuthenticationError", (Exception,), {"status_code": 401})(
        "Incorrect API key provided: sk-LEAKME-12345."
    )
    sdk = StubSDK(error=auth_error)
    async with api_client(_gateway(sdk)) as client:
        auth = await _login(client)
        response = await client.post(CHAT_PATH, json=BODY, headers=auth)
    assert response.status_code == 401
    body = response.json()
    assert ENVELOPE_KEYS == body.keys()
    assert body["code"] == "unauthenticated"
    assert "LEAKME" not in body["message"]  # vendor text never rides the envelope


async def test_over_cap_plan_is_refused_before_the_sdk_is_called() -> None:
    # 4096 planned output tokens at $2.50/Mtok = $0.0102 > the $0.005 cap.
    sdk = StubSDK()
    gateway = _gateway(sdk, spend_cap_usd=0.005)
    async with api_client(gateway) as client:
        auth = await _login(client)
        response = await client.post(
            CHAT_PATH,
            json={**BODY, "options": {"max_tokens": 4096}},
            headers=auth,
        )
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "permission_denied"
    assert "spend cap" in body["message"]
    assert sdk.calls == []  # money-gate: refused locally, vendor never saw a token


async def test_pinned_model_outside_allowlist_never_proxies() -> None:
    sdk = StubSDK()
    async with api_client(_gateway(sdk)) as client:
        auth = await _login(client)
        response = await client.post(
            CHAT_PATH, json={**BODY, "model": "gpt-5-ultra-preview"}, headers=auth
        )
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    assert sdk.calls == []  # the pin is checked before any SDK call


async def test_sentinel_auto_follows_the_gated_default() -> None:
    sdk = StubSDK()
    async with api_client(_gateway(sdk)) as client:
        auth = await _login(client)
        response = await client.post(CHAT_PATH, json={**BODY, "model": AUTO_MODEL}, headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "openai"
    assert sdk.calls[0]["model"] == "openai/gpt-4o-mini"


async def test_anthropic_selection_resolves_under_its_own_identity() -> None:
    sdk = StubSDK()
    gateway = _gateway(sdk, provider="anthropic", model="claude-3-5-sonnet-20241022")
    assert gateway.registry.default_key == "anthropic"
    async with api_client(gateway) as client:
        auth = await _login(client)
        response = await client.post(CHAT_PATH, json=BODY, headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "anthropic"


def test_adapter_isinstance_of_the_day_015_port() -> None:
    """Structural conformance, asserted where the port is runtime-checkable."""
    from app.gateway.contracts import ChatProvider

    adapter = LiteLLMAdapter(
        provider="openai",
        model="gpt-4o-mini",
        credential=SecretStr("x"),
        allowlist=build_allowlist(["openai/gpt-4o-mini"]),
        client=StubSDK(),
    )
    assert isinstance(adapter, ChatProvider)
    fake_registry_provider = build_registry(
        make_settings(
            environment="test",
            secret_key=TEST_SECRET_KEY,
            database_url="",
            redis_url="",
        )
    ).resolve("fake")
    assert isinstance(fake_registry_provider, ChatProvider)
