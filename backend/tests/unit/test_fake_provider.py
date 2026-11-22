"""Day 015 unit tests: deterministic fake providers and the chat gateway.

These pin the *internal* contract every later adapter must satisfy, with no
HTTP and no network: the seven fake scenarios each produce exactly one
normalized outcome, usage carries its truth label, provider failures carry the
standard code/HTTP/retryable mapping, and the gateway service enforces the
deadline and measures latency itself rather than trusting an adapter's hint.

Selection is always by explicit fixture/config (a constructed
:class:`FakeBehavior` or a seeded registry), never by monkeypatched globals or
randomness — the acceptance check "deterministic and selectable without network
access" is what most of these tests are really asserting.
"""

from __future__ import annotations

import asyncio

import pytest
from app.core.errors import AppError
from app.gateway.contracts import (
    FAKE_MODEL,
    FAKE_PROVIDER,
    GATEWAY_ERROR_SPECS,
    ChatMessageView,
    ChatProvider,
    CompletionRequest,
    GatewayErrorCode,
    InternalProviderError,
    ProviderCompletion,
    ProviderFailure,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderUsage,
    RateLimitedProviderError,
    UsageKind,
)
from app.gateway.fakes import DeterministicFakeProvider, FakeBehavior, FakeScenario
from app.gateway.registry import (
    NoProviderConfiguredError,
    ProviderRegistry,
    build_fake_registry,
    fake_scenario_key,
)
from app.gateway.service import ChatGateway


def _request(**overrides: object) -> CompletionRequest:
    base: dict[str, object] = {
        "provider": FAKE_PROVIDER,
        "model": FAKE_MODEL,
        "messages": (ChatMessageView(role="user", content="hello"),),
        "max_output_tokens": 256,
        "timeout_ms": 5_000,
        "trace_id": "trace-abc",
    }
    base.update(overrides)
    return CompletionRequest(**base)  # type: ignore[arg-type]


async def _noop(_seconds: float) -> None:
    """Sleeper that never actually waits, for deterministic scenario tests."""


# ---------------------------------------------------------------------------
# Success / determinism
# ---------------------------------------------------------------------------


async def test_success_fake_returns_normalized_completion() -> None:
    provider = DeterministicFakeProvider(FakeBehavior(), sleep=_noop)
    result = await provider.complete(_request())
    assert isinstance(result, ProviderCompletion)
    assert result.provider == FAKE_PROVIDER
    assert result.model == FAKE_MODEL
    assert result.finish_reason == "stop"
    assert result.trace_id == "trace-abc"
    assert result.usage.kind is UsageKind.EXACT
    usage = result.usage
    assert usage.input_tokens is not None and usage.output_tokens is not None
    assert usage.total_tokens == usage.input_tokens + usage.output_tokens


async def test_success_is_repeatable_for_identical_fixture() -> None:
    behavior = FakeBehavior()
    provider = DeterministicFakeProvider(behavior, sleep=_noop)
    first = await provider.complete(_request())
    second = await provider.complete(_request())
    assert first == second


async def test_model_pin_is_echoed_but_blank_resolves_to_default() -> None:
    provider = DeterministicFakeProvider(FakeBehavior(), sleep=_noop)
    pinned = await provider.complete(_request(model="sentinel-fake-v9"))
    assert pinned.model == "sentinel-fake-v9"
    blank = await provider.complete(_request(model=""))
    assert blank.model == FAKE_MODEL


# ---------------------------------------------------------------------------
# Each failure scenario maps to one typed ProviderFailure + standard code
# ---------------------------------------------------------------------------

_EXPECTED_FAILURE: dict[FakeScenario, tuple[type[ProviderFailure], GatewayErrorCode]] = {
    FakeScenario.RATE_LIMIT_429: (RateLimitedProviderError, GatewayErrorCode.RATE_LIMITED),
    FakeScenario.SERVER_ERROR_500: (InternalProviderError, GatewayErrorCode.INTERNAL_ERROR),
    FakeScenario.MALFORMED: (InternalProviderError, GatewayErrorCode.INTERNAL_ERROR),
    FakeScenario.EMPTY: (InternalProviderError, GatewayErrorCode.INTERNAL_ERROR),
    FakeScenario.RESET: (ProviderUnavailableError, GatewayErrorCode.PROVIDER_UNAVAILABLE),
}


@pytest.mark.parametrize("scenario", list(_EXPECTED_FAILURE))
async def test_failure_scenarios_raise_typed_normalized_errors(
    scenario: FakeScenario,
) -> None:
    expected_class, expected_code = _EXPECTED_FAILURE[scenario]
    provider = DeterministicFakeProvider(FakeBehavior(scenario=scenario), sleep=_noop)
    with pytest.raises(expected_class) as excinfo:
        await provider.complete(_request())
    failure = excinfo.value
    assert isinstance(failure, ProviderFailure)
    assert isinstance(failure, AppError)  # rides the standard envelope
    assert failure.gateway_code is expected_code
    assert failure.status_code == GATEWAY_ERROR_SPECS[expected_code].http_status
    assert failure.code == GATEWAY_ERROR_SPECS[expected_code].envelope_code
    assert failure.retryable is GATEWAY_ERROR_SPECS[expected_code].retryable


async def test_rate_limit_carries_retry_after() -> None:
    provider = DeterministicFakeProvider(
        FakeBehavior(scenario=FakeScenario.RATE_LIMIT_429, retry_after_seconds=7), sleep=_noop
    )
    with pytest.raises(RateLimitedProviderError) as excinfo:
        await provider.complete(_request())
    assert excinfo.value.retry_after_seconds == 7


async def test_failure_str_names_standard_code_without_leaking() -> None:
    provider = DeterministicFakeProvider(FakeBehavior(scenario=FakeScenario.EMPTY), sleep=_noop)
    with pytest.raises(InternalProviderError) as excinfo:
        await provider.complete(_request())
    # repr/str shows the contract code + generic message, never a vendor body.
    assert str(excinfo.value).startswith("INTERNAL_ERROR:")


# ---------------------------------------------------------------------------
# Latency scenario parks deterministically via the injected sleeper
# ---------------------------------------------------------------------------


async def test_latency_scenario_awaits_configured_duration() -> None:
    recorded: list[float] = []

    async def recorder(seconds: float) -> None:
        recorded.append(seconds)

    provider = DeterministicFakeProvider(
        FakeBehavior(scenario=FakeScenario.LATENCY, latency_ms=1_500), sleep=recorder
    )
    result = await provider.complete(_request())
    assert recorded == [1.5]
    assert isinstance(result, ProviderCompletion)


# ---------------------------------------------------------------------------
# Registry selection + production dark registry
# ---------------------------------------------------------------------------


def test_fake_registry_files_all_scenarios_and_aliases_default() -> None:
    registry = build_fake_registry(FakeScenario.RESET, sleep=_noop)
    assert fake_scenario_key(FakeScenario.RESET) in registry.names
    assert registry.default_key == FAKE_PROVIDER
    # The "fake" alias resolves to the settings-selected default scenario.
    chosen = registry.resolve(None)
    assert isinstance(chosen, DeterministicFakeProvider)
    assert chosen.behavior.scenario is FakeScenario.RESET


def test_usage_total_is_none_when_either_count_unknown() -> None:
    assert ProviderUsage(kind=UsageKind.UNKNOWN).total_tokens is None
    assert ProviderUsage(kind=UsageKind.ESTIMATED, input_tokens=10).total_tokens is None


async def test_empty_registry_resolves_to_a_safe_outage() -> None:
    registry = ProviderRegistry()  # production posture: dark
    with pytest.raises(NoProviderConfiguredError) as excinfo:
        registry.resolve(None)
    assert isinstance(excinfo.value, AppError)
    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "dependency_unavailable"


# ---------------------------------------------------------------------------
# Gateway service: measured latency, enforced deadline, reclassification
# ---------------------------------------------------------------------------


def _gateway(
    provider: ChatProvider,
    *,
    default_timeout_ms: int = 5_000,
    max_timeout_ms: int = 30_000,
) -> ChatGateway:
    registry = ProviderRegistry()
    registry.register(FAKE_PROVIDER, provider, make_default=True)
    return ChatGateway(
        registry, default_timeout_ms=default_timeout_ms, max_timeout_ms=max_timeout_ms
    )


async def test_gateway_overwrites_reported_latency_with_measured() -> None:
    # The fake reports latency_ms=0; the gateway replaces it with elapsed time.
    async def slow(_seconds: float) -> None:
        await asyncio.sleep(_seconds)

    provider = DeterministicFakeProvider(
        FakeBehavior(scenario=FakeScenario.LATENCY, latency_ms=40), sleep=slow
    )
    result = await _gateway(provider).complete(
        messages=[ChatMessageView(role="user", content="hi")], max_output_tokens=64
    )
    # Measured latency is >= the 40ms the adapter actually parked for.
    assert result.latency_ms >= 40


async def test_gateway_timeout_raises_provider_timeout() -> None:
    hang: asyncio.Future[None] = asyncio.Future()

    class _HangingProvider:
        @property
        def name(self) -> str:
            return FAKE_PROVIDER

        @property
        def default_model(self) -> str:
            return FAKE_MODEL

        async def complete(self, request: CompletionRequest) -> ProviderCompletion:
            await hang  # never resolves; the deadline must fire
            raise AssertionError("unreachable: the gateway deadline fires first")

    gateway = _gateway(_HangingProvider(), default_timeout_ms=50, max_timeout_ms=1_000)
    with pytest.raises(ProviderTimeoutError) as excinfo:
        await gateway.complete(
            messages=[ChatMessageView(role="user", content="hi")], max_output_tokens=64
        )
    assert excinfo.value.gateway_code is GatewayErrorCode.PROVIDER_TIMEOUT
    assert excinfo.value.status_code == 504
    assert excinfo.value.retryable is True


async def test_gateway_reclassifies_raw_errors_without_leaking() -> None:
    class _BoomProvider:
        @property
        def name(self) -> str:
            return "boom"

        @property
        def default_model(self) -> str:
            return "boom-model"

        async def complete(self, request: CompletionRequest) -> ProviderCompletion:
            raise RuntimeError("vendor secret detail with a DSN=postgres://secret")

    gateway = _gateway(_BoomProvider())
    with pytest.raises(InternalProviderError) as excinfo:
        await gateway.complete(
            messages=[ChatMessageView(role="user", content="hi")], max_output_tokens=64
        )
    # The generic message never carries the vendor's raw text.
    assert "secret" not in excinfo.value.message
    assert excinfo.value.gateway_code is GatewayErrorCode.INTERNAL_ERROR
