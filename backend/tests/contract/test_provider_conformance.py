"""Day 016 contract tests: every provider satisfies one normalized contract.

The step's core promise — "record adapter conformance tests so every provider
satisfies the same contract or reports unsupported capabilities honestly" —
is proven here as a *harness*: the same assertions run against the Day 015
fake and against three LiteLLM-adapter shapes (direct vendor, Anthropic, and
a local OpenAI-compatible endpoint), all driven through mocked SDK responses
so default CI makes **no paid or external provider call**.

What each parametrized check pins:

* success returns the same closed, normalized types with an honest usage
  label — and the gateway, not the adapter, owns the measured latency;
* the seven-code failure family is adapter-independent: vendor exceptions and
  unknown raw errors both surface as typed :class:`ProviderFailure` values
  whose HTTP/status/retryable triple comes from the shared spec table;
* capabilities are reported honestly (nobody claims streaming it lacks);
* the destination allowlist and per-call spend cap refuse *before* the SDK is
  touched — an SSRF-shaped pin or an over-cap plan never leaves the process.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from app.gateway.adapters.litellm_adapter import LiteLLMAdapter, supports_streaming
from app.gateway.allowlist import DestinationNotAllowedError, build_allowlist
from app.gateway.contracts import (
    AUTO_MODEL,
    GATEWAY_ERROR_SPECS,
    ChatMessageView,
    CompletionRequest,
    GatewayErrorCode,
    InternalProviderError,
    ProviderCompletion,
    ProviderFailure,
    UsageKind,
)
from app.gateway.fakes import DeterministicFakeProvider, FakeBehavior, FakeScenario
from app.gateway.registry import build_fake_registry
from app.gateway.service import ChatGateway
from pydantic import SecretStr

LOCAL_URL = "http://127.0.0.1:11434/v1"
MESSAGES = (ChatMessageView(role="user", content="conformance probe"),)


def _completion_response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="conformance answer"),
                finish_reason="stop",
            )
        ],
        model="concrete-deployment-v1",
        usage=SimpleNamespace(prompt_tokens=9, completion_tokens=4),
    )


class _StubSDK:
    """Module-shaped fake of ``litellm``: a callable seam plus error classes."""

    def __init__(self, *, response: Any = None, error: BaseException | None = None) -> None:
        self.response = response if response is not None else _completion_response()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def acompletion(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response

    # Exception ladder names the adapter keys on (shapes, not the real SDK).
    AuthenticationError = type("AuthenticationError", (Exception,), {"status_code": 401})
    Forbidden = type("Forbidden", (Exception,), {"status_code": 403})
    BadRequestError = type("BadRequestError", (Exception,), {"status_code": 400})
    RateLimitError = type("RateLimitError", (Exception,), {"status_code": 429})
    APITimeoutError = type("APITimeoutError", (Exception,), {})
    APIConnectionError = type("APIConnectionError", (Exception,), {})
    InternalServerError = type("InternalServerError", (APIConnectionError,), {})
    ServiceUnavailableError = type(
        "ServiceUnavailableError", (APIConnectionError,), {"status_code": 503}
    )
    APIError = type("APIError", (Exception,), {})


def _adapter(provider: str, model: str, base_url: str | None, sdk: Any) -> LiteLLMAdapter:
    entry = f"{provider}/{model}" + (f"@{base_url}" if base_url else "")
    return LiteLLMAdapter(
        provider=provider,
        model=model,
        base_url=base_url,
        credential=SecretStr("cred") if base_url is None and provider == "openai" else None,
        allowlist=build_allowlist([entry]),
        client=sdk,
    )


#: Provider-under-test factories: (name, model, factory). The fake comes from
#: Day 015; the others are the LiteLLM adapter's three deployment shapes.
def _providers() -> list[tuple[str, str, Callable[[Any], tuple[object, Any]]]]:
    def fake(_sdk: Any) -> tuple[object, Any]:
        registry = build_fake_registry(FakeScenario.SUCCESS)
        return registry.resolve("fake"), registry

    def openai(sdk: Any) -> tuple[object, Any]:
        return _adapter("openai", "gpt-4o-mini", None, sdk), sdk

    def anthropic(sdk: Any) -> tuple[object, Any]:
        return _adapter("anthropic", "claude-3-5-sonnet-20241022", None, sdk), sdk

    def local(sdk: Any) -> tuple[object, Any]:
        return _adapter("openai", "qwen2.5", LOCAL_URL, sdk), sdk

    return [
        ("fake", "sentinel-fake-v1", fake),
        ("openai", "gpt-4o-mini", openai),
        ("anthropic", "claude-3-5-sonnet-20241022", anthropic),
        ("local-openai-compatible", "qwen2.5", local),
    ]


PROVIDERS = _providers()
_PROVIDER_IDS = [name for name, _, _ in PROVIDERS]
_FACTORY = {name: (model, factory) for name, model, factory in PROVIDERS}


def _gateway_for(name: str, sdk: Any) -> ChatGateway:
    model, factory = _FACTORY[name]
    provider, _ = factory(sdk)
    from app.gateway.registry import ProviderRegistry

    registry = ProviderRegistry()
    registry.register(name, provider, make_default=True)  # type: ignore[arg-type]
    return ChatGateway(registry, default_timeout_ms=5_000, max_timeout_ms=30_000)


# ---------------------------------------------------------------------------
# Normalized success is adapter-independent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", _PROVIDER_IDS)
async def test_success_yields_the_same_normalized_shape(name: str) -> None:
    sdk = _StubSDK()
    result = await _gateway_for(name, sdk).complete(
        messages=list(MESSAGES), max_output_tokens=64, trace_id="conf-1"
    )
    assert isinstance(result, ProviderCompletion)
    assert isinstance(result.content, str) and result.content
    assert result.finish_reason == "stop"
    assert result.usage.kind in {UsageKind.EXACT, UsageKind.ESTIMATED, UsageKind.UNKNOWN}
    assert result.trace_id == "conf-1"
    # The gateway measures latency itself; adapter claims are hints at most.
    assert isinstance(result.latency_ms, int) and result.latency_ms >= 0
    if name != "fake":
        assert sdk.calls, "real-shaped adapter must reach the SDK seam"


@pytest.mark.parametrize("name", ["openai", "anthropic", "local-openai-compatible"])
async def test_vendor_usage_counts_are_exact_and_total_is_derived(name: str) -> None:
    sdk = _StubSDK()
    result = await _gateway_for(name, sdk).complete(messages=list(MESSAGES), max_output_tokens=64)
    assert result.usage.kind is UsageKind.EXACT
    assert result.usage.total_tokens == 13  # 9 + 4, derived not trusted


@pytest.mark.parametrize("name", ["openai", "anthropic", "local-openai-compatible"])
async def test_missing_vendor_usage_is_estimated_not_faked(name: str) -> None:
    no_usage = _completion_response()
    no_usage.usage = None
    sdk = _StubSDK(response=no_usage)
    result = await _gateway_for(name, sdk).complete(messages=list(MESSAGES), max_output_tokens=64)
    assert result.usage.kind is UsageKind.ESTIMATED
    assert result.usage.input_tokens and result.usage.output_tokens


@pytest.mark.parametrize("name", _PROVIDER_IDS)
async def test_sentinel_auto_resolves_to_the_providers_model(name: str) -> None:
    sdk = _StubSDK()
    result = await _gateway_for(name, sdk).complete(
        messages=list(MESSAGES), model=AUTO_MODEL, max_output_tokens=64
    )
    # The adapter's concrete deployment identity, never the virtual alias.
    assert result.model and result.model != AUTO_MODEL


# ---------------------------------------------------------------------------
# Failure family is one contract, not four dialects
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["openai", "anthropic", "local-openai-compatible"])
@pytest.mark.parametrize(
    ("error_name", "gateway_code"),
    [
        ("AuthenticationError", GatewayErrorCode.UNAUTHORIZED),
        ("Forbidden", GatewayErrorCode.FORBIDDEN),
        ("BadRequestError", GatewayErrorCode.INVALID_REQUEST),
        ("RateLimitError", GatewayErrorCode.RATE_LIMITED),
        ("APITimeoutError", GatewayErrorCode.PROVIDER_TIMEOUT),
        ("ServiceUnavailableError", GatewayErrorCode.PROVIDER_UNAVAILABLE),
        ("APIError", GatewayErrorCode.INTERNAL_ERROR),
    ],
)
async def test_vendor_errors_collapse_to_the_standard_specs(
    name: str, error_name: str, gateway_code: GatewayErrorCode
) -> None:
    sdk = _StubSDK()
    error_cls = getattr(sdk, error_name)
    sdk.error = error_cls(f"vendor detail for {error_name}")
    with pytest.raises(ProviderFailure) as caught:
        await _gateway_for(name, sdk).complete(messages=list(MESSAGES), max_output_tokens=64)
    failure = caught.value
    assert failure.gateway_code is gateway_code
    spec = GATEWAY_ERROR_SPECS[gateway_code]
    assert failure.status_code == spec.http_status
    assert failure.code == spec.envelope_code
    assert "vendor detail" not in str(failure)


@pytest.mark.parametrize("name", ["openai", "anthropic", "local-openai-compatible"])
async def test_unknown_raw_errors_never_escape_the_port(name: str) -> None:
    """Even an error the adapter has never seen becomes a typed failure —
    via the service's final reclassification if the adapter cannot place it."""
    sdk = _StubSDK()
    sdk.error = KeyError("Authorization header Bearer sk-leak-me")
    with pytest.raises(ProviderFailure) as caught:
        await _gateway_for(name, sdk).complete(messages=list(MESSAGES), max_output_tokens=64)
    assert isinstance(caught.value, InternalProviderError)
    assert "sk-leak-me" not in str(caught.value)


async def test_empty_completion_fails_typed_on_adapters_but_not_the_fake() -> None:
    """Adapter honesty: a well-formed HTTP 200 with no content is a typed
    failure. The fake ignores vendor responses by design (its reply comes from
    its fixture), so conformance asserts the *adapter* behavior and pins the
    fake's determinism separately — same port, different source of truth."""
    empty = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=""), finish_reason="stop")],
        model="m",
        usage=None,
    )
    for name in ["openai", "anthropic", "local-openai-compatible"]:
        sdk = _StubSDK(response=empty)
        with pytest.raises(ProviderFailure) as caught:
            await _gateway_for(name, sdk).complete(messages=list(MESSAGES), max_output_tokens=64)
        assert caught.value.gateway_code is GatewayErrorCode.INTERNAL_ERROR

    sdk = _StubSDK(response=empty)
    fake_result = await _gateway_for("fake", sdk).complete(
        messages=list(MESSAGES), max_output_tokens=64
    )
    assert fake_result.content  # fixture-backed success, never vendor-shaped


# ---------------------------------------------------------------------------
# Honest capabilities and closed destinations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", _PROVIDER_IDS)
def test_streaming_is_reported_as_unsupported_honestly(name: str) -> None:
    _model, factory = _FACTORY[name]
    provider, _ = factory(_StubSDK())
    assert supports_streaming(provider) is False
    declared = getattr(provider, "capabilities", None)
    if declared is not None:
        assert declared.get("streaming") is False


@pytest.mark.parametrize("name", ["openai", "anthropic", "local-openai-compatible"])
async def test_model_pin_outside_the_allowlist_stops_before_the_sdk(name: str) -> None:
    sdk = _StubSDK()
    gateway = _gateway_for(name, sdk)
    with pytest.raises(ProviderFailure) as caught:
        await gateway.complete(
            messages=list(MESSAGES), model="evil/remote-proxy", max_output_tokens=64
        )
    assert caught.value.gateway_code is GatewayErrorCode.FORBIDDEN
    assert sdk.calls == []  # SSRF pin never reached any client


async def test_spend_cap_refuses_locally_before_vendor_sees_a_token() -> None:
    sdk = _StubSDK()
    # An adapter whose planned worst-case output cannot fit the configured cap:
    strict = LiteLLMAdapter(
        provider="openai",
        model="gpt-4o-mini",
        credential=SecretStr("cred"),
        allowlist=build_allowlist(["openai/gpt-4o-mini"]),
        spend_cap_usd=0.0001,
        price_per_mtok_output=2.50,
        client=sdk,
    )
    from app.gateway.registry import ProviderRegistry

    registry = ProviderRegistry()
    registry.register("openai", strict, make_default=True)
    gateway = ChatGateway(registry, default_timeout_ms=5_000, max_timeout_ms=30_000)
    with pytest.raises(ProviderFailure) as caught:
        await gateway.complete(messages=list(MESSAGES), max_output_tokens=8_192)
    assert caught.value.gateway_code is GatewayErrorCode.FORBIDDEN
    assert "spend cap" in str(caught.value)
    assert sdk.calls == []


def test_local_destination_requires_its_exact_base_url() -> None:
    sdk = _StubSDK()
    adapter = _adapter("openai", "qwen2.5", LOCAL_URL, sdk)
    assert adapter.capabilities["streaming"] is False
    # The same model at the *vendor* endpoint is a different destination:
    with pytest.raises(DestinationNotAllowedError):
        build_allowlist(["openai/qwen2.5"]).require("openai", "qwen2.5", LOCAL_URL)


# ---------------------------------------------------------------------------
# The fake and the adapter land on the same wire (registry-level equality)
# ---------------------------------------------------------------------------


async def test_fake_and_adapter_share_one_registry_and_envelope() -> None:
    """Day 015's fake and Day 016's adapter coexist in one registry; switching
    the default is a build decision, and both answer through the same
    normalized types — the precondition for Day 017's ledger and Day 019's
    router treating providers interchangeably."""
    sdk = _StubSDK()
    registry = build_fake_registry(FakeScenario.SUCCESS)
    adapter = _adapter("openai", "gpt-4o-mini", None, sdk)
    registry.register("openai", adapter, make_default=True)
    gateway = ChatGateway(registry, default_timeout_ms=5_000, max_timeout_ms=30_000)

    via_fake = await gateway.complete(
        messages=list(MESSAGES), max_output_tokens=64, provider_key="fake"
    )
    via_openai = await gateway.complete(messages=list(MESSAGES), max_output_tokens=64)
    assert via_fake.provider == "fake"
    assert via_openai.provider == "openai"
    assert type(via_fake) is type(via_openai) is ProviderCompletion
    assert via_fake.usage.kind is UsageKind.EXACT
    assert via_openai.usage.kind is UsageKind.EXACT


async def test_latency_scenario_still_parks_through_gateway_sleeper() -> None:
    """Conformance of the *sleep seam*: the fake's injected sleeper proves the
    Day 015 latency contract survives registry coexistence with adapters."""
    slept: list[float] = []

    async def recorder(seconds: float) -> None:
        slept.append(seconds)

    registry = build_fake_registry(FakeScenario.LATENCY, sleep=recorder)
    provider = registry.resolve("fake.latency")
    assert isinstance(provider, DeterministicFakeProvider)
    result = await provider.complete(
        CompletionRequest(
            provider="fake",
            model="sentinel-fake-v1",
            messages=MESSAGES,
            max_output_tokens=64,
            timeout_ms=5_000,
        )
    )
    assert slept == [FakeBehavior(scenario=FakeScenario.LATENCY).latency_ms / 1000.0]
    assert result.usage.kind is UsageKind.EXACT
