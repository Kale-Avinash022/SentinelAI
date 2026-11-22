"""Day 016 unit tests: the LiteLLM adapter and its gated wiring.

Everything here runs with **zero network and zero spend**: the SDK is replaced
by an injected fake client (the adapter's ``client`` seam), so default CI never
even imports ``litellm``. The suite proves the four adapter duties the step
demands — normalized translation in both directions, allowlisted destinations,
secret-adapter-only credentials with a pre-call spend cap, and honest
capability/usage labels — plus the pure helpers (URL/destination parsing,
credential references) and the settings-gated ``build_registry`` decision tree.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from app.configs.vault import FernetVault
from app.gateway.adapters.litellm_adapter import (
    CHARS_PER_TOKEN_ESTIMATE,
    LiteLLMAdapter,
    supports_streaming,
)
from app.gateway.allowlist import (
    DestinationNotAllowedError,
    build_allowlist,
    is_loopback_base_url,
    normalize_base_url,
    parse_destination,
)
from app.gateway.contracts import (
    ChatMessageView,
    CompletionRequest,
    ForbiddenProviderError,
    InternalProviderError,
    InvalidRequestProviderError,
    ProviderFailure,
    ProviderUnavailableError,
    RateLimitedProviderError,
    UnauthorizedProviderError,
    UsageKind,
)
from app.gateway.credentials import (
    CredentialNotConfiguredError,
    EnvironmentCredentialResolver,
)
from app.gateway.registry import NoProviderConfiguredError, build_registry
from app.gateway.service import ChatGateway, build_gateway
from pydantic import SecretStr

from tests.conftest import TEST_SECRET_KEY, make_settings

MODEL = "gpt-4o-mini"
BASE_ALLOWLIST = build_allowlist([f"openai/{MODEL}", "anthropic/claude-3-5-sonnet-20241022"])


def _request(
    content: str = "hello sentinel", *, model: str = MODEL, max_tokens: int = 256
) -> CompletionRequest:
    return CompletionRequest(
        provider="openai",
        model=model,
        messages=(ChatMessageView(role="user", content=content),),
        max_output_tokens=max_tokens,
        timeout_ms=5_000,
        trace_id="trace-unit",
    )


# ---------------------------------------------------------------------------
# Fake SDK client seams
# ---------------------------------------------------------------------------


def _ok_response(
    *,
    content: str = "normalized answer",
    model: str = "gpt-4o-mini-2024-07-18",
    usage: Any = ...,
    finish_reason: str = "stop",
) -> SimpleNamespace:
    token_usage = None if usage is None else usage
    if usage is ...:
        token_usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7)
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)
        ],
        model=model,
        usage=token_usage,
    )


class _FakeClient:
    """Stands in for the ``litellm`` module: records calls, returns/raises."""

    def __init__(self, *, response: Any = None, error: BaseException | None = None) -> None:
        self.response = response if response is not None else _ok_response()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def acompletion(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response

    @property
    def last(self) -> dict[str, Any]:
        assert self.calls, "adapter never reached the SDK"
        return self.calls[-1]


def _adapter(client: Any, **overrides: Any) -> LiteLLMAdapter:
    params: dict[str, Any] = {
        "provider": "openai",
        "model": MODEL,
        "allowlist": BASE_ALLOWLIST,
        "client": client,
    }
    params.update(overrides)
    return LiteLLMAdapter(**params)


# Exception shapes mirroring litellm's public ladder (names are what the
# adapter keys on; status_code is the attribute LiteLLM mirrors from vendors).


class AuthenticationError(Exception):
    status_code = 401


class ForbiddenError(Exception):
    status_code = 403


class BadRequestError(Exception):
    status_code = 400


class RateLimitError(Exception):
    status_code = 429


class APITimeoutError(Exception):
    """LiteLLM keeps this distinct from connection errors; so does the map."""


class APIConnectionError(Exception):
    pass


class InternalServerError(Exception):
    """LiteLLM's 5xx parent — connection-shaped by its own class doc."""


class ServiceUnavailableError(APIConnectionError):
    status_code = 503


class VendorAPIError(Exception):
    status_code = 500


def _litellm_namespace(*classes: type[Exception]) -> SimpleNamespace:
    return SimpleNamespace(acompletion=None, **{c.__name__: c for c in classes})


def _client_with_errors(error: BaseException, *classes: type[Exception]) -> _FakeClient:
    client = _FakeClient(error=error)
    for cls in classes:
        setattr(client, cls.__name__, cls)
    return client


# ---------------------------------------------------------------------------
# Success normalization
# ---------------------------------------------------------------------------


async def test_exact_usage_and_identity_are_normalized() -> None:
    client = _FakeClient()
    result = await _adapter(client).complete(_request())
    assert result.content == "normalized answer"
    assert result.provider == "openai"
    # Vendor-reported identity wins over the request pin.
    assert result.model == "gpt-4o-mini-2024-07-18"
    assert result.usage.kind is UsageKind.EXACT
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 7
    assert result.usage.total_tokens == 18
    assert result.finish_reason == "stop"
    assert result.trace_id == "trace-unit"
    assert result.latency_ms >= 0


async def test_missing_usage_is_estimated_and_labelled_never_exact() -> None:
    client = _FakeClient(response=_ok_response(content="x" * 40, usage=None))
    result = await _adapter(client).complete(_request("y" * 40, max_tokens=64))
    assert result.usage.kind is UsageKind.ESTIMATED
    assert result.usage.input_tokens == max(1, 40 // CHARS_PER_TOKEN_ESTIMATE)
    assert result.usage.output_tokens == max(1, 40 // CHARS_PER_TOKEN_ESTIMATE)
    assert result.usage.total_tokens == 20


async def test_partial_usage_is_estimated_both_sides() -> None:
    client = _FakeClient(response=_ok_response(usage=SimpleNamespace(prompt_tokens=99)))
    result = await _adapter(client).complete(_request())
    assert result.usage.kind is UsageKind.ESTIMATED
    # The partial vendor count is NOT laundered into an exact bill.
    assert result.usage.input_tokens != 99


async def test_build_kwargs_match_vendor_addressing_and_never_carry_secrets() -> None:
    client = _FakeClient()
    adapter = _adapter(client, credential=SecretStr("sk-super-secret-value"))
    await adapter.complete(_request(model=MODEL))
    last = client.last
    assert last["model"] == f"openai/{MODEL}"
    assert last["max_tokens"] == 256
    assert last["timeout"] == 5.0
    assert last["api_key"] == "sk-super-secret-value"
    assert not any("super-secret" in str(value) for key, value in last.items() if key != "api_key")
    assert last["messages"][0] == {"role": "user", "content": "hello sentinel"}


async def test_base_url_override_is_forwarded_and_mock_routes_to_sdk_mock() -> None:
    local_url = "http://127.0.0.1:11434/v1"
    allowlist = build_allowlist([f"openai/local-qwen@{local_url}"])
    client = _FakeClient()
    adapter = _adapter(
        client,
        model="local-qwen",
        base_url=local_url,
        allowlist=allowlist,
        credential=SecretStr("unused-in-mock"),
        mock_mode=True,
    )
    await adapter.complete(_request(model="local-qwen"))
    last = client.last
    assert last["api_base"] == local_url
    assert last["model"] == "local-qwen"  # custom endpoint: bare model id
    assert last["mock_response"] == "mocked deterministic completion"
    assert "api_key" not in last  # mock mode never hands a credential to the SDK


# ---------------------------------------------------------------------------
# Failure translation — every vendor error becomes exactly one typed failure
# ---------------------------------------------------------------------------


def _expect(
    error: BaseException,
    classes: tuple[type[Exception], ...],
    failure: type[ProviderFailure] | None,
) -> tuple[BaseException, tuple[type[Exception], ...], type[ProviderFailure] | None]:
    return (error, classes, failure)


_CASES = [
    _expect(
        AuthenticationError("Invalid API key provided: sk-abc123def456"),
        (AuthenticationError,),
        UnauthorizedProviderError,
    ),
    _expect(ForbiddenError("model access denied"), (ForbiddenError,), ForbiddenProviderError),
    _expect(
        BadRequestError("unknown parameter 'sneaky'"),
        (BadRequestError,),
        InvalidRequestProviderError,
    ),
    _expect(RateLimitError("Too Many Requests"), (RateLimitError,), RateLimitedProviderError),
    _expect(APITimeoutError("Timed out"), (APITimeoutError,), None),
    _expect(
        APIConnectionError("Connection error."), (APIConnectionError,), ProviderUnavailableError
    ),
    _expect(InternalServerError("Server error"), (InternalServerError,), ProviderUnavailableError),
    _expect(
        ServiceUnavailableError("no capacity"), (ServiceUnavailableError,), ProviderUnavailableError
    ),
    _expect(VendorAPIError("upstream 500"), (VendorAPIError,), InternalProviderError),
    # Connection-shaped errors are recognized by exception *class name*, in
    # step with the "never trust vendor text" rule: a message saying "socket
    # hang up" on a non-connection class stays INTERNAL_ERROR.
    _expect(RuntimeError("socket hang up"), (), InternalProviderError),
    _expect(ConnectionResetError("connection reset by peer"), (), ProviderUnavailableError),
    _expect(ValueError("boom"), (), InternalProviderError),
]


@pytest.mark.parametrize("error,classes,failure", _CASES)
async def test_vendor_error_maps_to_typed_failure_without_leaking_text(
    error: BaseException,
    classes: tuple[type[Exception], ...],
    failure: type[ProviderFailure] | None,
) -> None:
    client = _client_with_errors(error, *classes)
    with pytest.raises(ProviderFailure) as caught:
        await _adapter(client).complete(_request())
    raised = caught.value
    if failure is not None:
        assert isinstance(raised, failure)
    # No vendor payload text rides the safe message or the chain we expose.
    assert "sk-abc123def456" not in str(raised)
    assert "upstream" not in str(raised)


async def test_timeout_before_first_token_is_provider_timeout() -> None:
    error = APITimeoutError("Request timed out.")
    client = _client_with_errors(error, APITimeoutError)
    with pytest.raises(ProviderFailure) as caught:
        await _adapter(client).complete(_request())
    failure = caught.value
    assert failure.gateway_code.value == "PROVIDER_TIMEOUT"
    assert failure.status_code == 504
    assert failure.retryable is True


async def test_rate_limit_carries_retry_after() -> None:
    error = RateLimitError("Too Many Requests")
    error.retry_after = 7  # type: ignore[attr-defined]
    client = _client_with_errors(error, RateLimitError)
    with pytest.raises(RateLimitedProviderError) as caught:
        await _adapter(client).complete(_request())
    assert caught.value.retry_after_seconds == 7


async def test_status_code_only_error_still_maps() -> None:
    """Custom-endpoint SDKs may raise plain exceptions carrying only status."""

    class Bare(Exception):
        status_code = 401

    client = _FakeClient(error=Bare("unauthorized"))
    with pytest.raises(UnauthorizedProviderError):
        await _adapter(client).complete(_request())


async def test_empty_completion_is_typed_internal_error() -> None:
    client = _FakeClient(response=_ok_response(content=""))
    with pytest.raises(InternalProviderError) as caught:
        await _adapter(client).complete(_request())
    assert "empty" in str(caught.value).lower()


async def test_malformed_completion_is_typed_internal_error() -> None:
    client = _FakeClient(response=SimpleNamespace(choices=None, model=None, usage=None))
    with pytest.raises(InternalProviderError):
        await _adapter(client).complete(_request())


# ---------------------------------------------------------------------------
# Closed capabilities, destinations and money
# ---------------------------------------------------------------------------


async def test_stream_option_is_honestly_refused_before_the_sdk() -> None:
    client = _FakeClient()
    with pytest.raises(InvalidRequestProviderError):
        await _adapter(client).complete(replace(_request(), options={"stream": True}))
    assert client.calls == []


def test_capabilities_report_streaming_false_and_probe_agrees() -> None:
    client = _FakeClient()
    adapter = _adapter(client)
    assert adapter.capabilities == {"streaming": False, "usage_counting": True}
    assert supports_streaming(adapter) is False


async def test_model_pin_outside_allowlist_never_reaches_sdk() -> None:
    client = _FakeClient()
    with pytest.raises(ForbiddenProviderError):
        await _adapter(client).complete(_request(model="gpt-4o-turbo-ultra"))
    assert client.calls == []


async def test_spend_cap_refuses_before_any_call() -> None:
    client = _FakeClient()
    adapter = _adapter(client, spend_cap_usd=0.01, price_per_mtok_output=2.50)
    with pytest.raises(ForbiddenProviderError) as caught:
        await adapter.complete(_request(max_tokens=4096))  # worst case $0.0102 > $0.01
    assert "spend cap" in str(caught.value)
    assert client.calls == []


async def test_spend_cap_allows_when_plan_fits() -> None:
    client = _FakeClient()
    adapter = _adapter(client, spend_cap_usd=0.05, price_per_mtok_output=2.50)
    result = await adapter.complete(_request(max_tokens=256))
    assert result.content == "normalized answer"


def test_constructor_rejects_unallowlisted_destination() -> None:
    with pytest.raises(DestinationNotAllowedError):
        _adapter(_FakeClient(), model="not-listed")


# ---------------------------------------------------------------------------
# Allowlist and URL parsing (pure)
# ---------------------------------------------------------------------------


def test_normalize_base_url_accepts_https_and_loopback_http() -> None:
    assert normalize_base_url("https://API.OpenAI.com/v1/") == "https://api.openai.com/v1"
    assert normalize_base_url("http://localhost:11434/v1") == "http://localhost:11434/v1"
    assert is_loopback_base_url("http://127.0.0.1:8000/v1") is True


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.example.com/v1",  # plain http off-loopback: SSRF
        "ftp://127.0.0.1/v1",  # unknown scheme
        "https://user:pass@host.example/v1",  # embedded credentials
        "http://192.168.0.7:11434/v1",  # private non-loopback http
    ],
)
def test_normalize_base_url_refuses_dangerous_shapes(url: str) -> None:
    with pytest.raises(DestinationNotAllowedError):
        normalize_base_url(url)


def test_parse_destination_uses_rightmost_slash_rule() -> None:
    entry = parse_destination("anthropic/claude-3-5-sonnet-20241022")
    assert (entry.provider, entry.model, entry.base_url) == (
        "anthropic",
        "claude-3-5-sonnet-20241022",
        None,
    )
    with_base = parse_destination("openai/local@https://models.example/v1")
    assert with_base.base_url == "https://models.example/v1"
    with pytest.raises(DestinationNotAllowedError):
        parse_destination("no-slash-here")


def test_allowlist_require_never_echoes_its_contents() -> None:
    allowlist = build_allowlist(["openai/secret-model"])
    with pytest.raises(DestinationNotAllowedError) as caught:
        allowlist.require("openai", "other-model")
    assert "secret-model" not in str(caught.value)


# ---------------------------------------------------------------------------
# Credential resolution through the secret adapter
# ---------------------------------------------------------------------------


def test_resolver_reads_raw_env_value_by_reference() -> None:
    resolver = EnvironmentCredentialResolver(env={"OPENAI_API_KEY": "sk-raw-value"})
    secret = resolver.resolve("OPENAI_API_KEY")
    assert secret.get_secret_value() == "sk-raw-value"
    assert "sk-raw-value" not in repr(secret)


def test_resolver_unseals_vault_referenced_secret() -> None:
    vault = FernetVault(TEST_SECRET_KEY)
    sealed = vault.seal("sk-vaulted-value")
    resolver = EnvironmentCredentialResolver(vault=vault, env={"ANTHROPIC_API_KEY": sealed})
    assert resolver.resolve("ANTHROPIC_API_KEY").get_secret_value() == "sk-vaulted-value"


def test_resolver_failures_are_opaque() -> None:
    resolver = EnvironmentCredentialResolver(env={})
    with pytest.raises(CredentialNotConfiguredError):
        resolver.resolve("OPENAI_API_KEY")
    sealed = FernetVault("another-master-secret-0123456789-0123456789-ab").seal("sk-x")
    no_vault = EnvironmentCredentialResolver(env={"K": sealed})
    with pytest.raises(CredentialNotConfiguredError):
        no_vault.resolve("K")
    wrong_vault = EnvironmentCredentialResolver(
        vault=FernetVault(TEST_SECRET_KEY), env={"K": sealed}
    )
    with pytest.raises(CredentialNotConfiguredError):
        wrong_vault.resolve("K")


# ---------------------------------------------------------------------------
# build_registry: the settings gate decision tree
# ---------------------------------------------------------------------------


class _AlwaysResolver:
    def resolve(self, reference: str) -> SecretStr:
        return SecretStr(f"resolved-for-{reference}")


class _MissingResolver:
    def resolve(self, reference: str) -> Any:
        raise CredentialNotConfiguredError(reference)


def _provider_settings(**overrides: Any) -> Any:
    # Any-typed like make_settings itself: pydantic-settings' init-only
    # ``_env_file`` kwarg is invisible to static typing.
    base: dict[str, Any] = {
        "environment": "test",
        "secret_key": TEST_SECRET_KEY,
        "database_url": "",
        "redis_url": "",
    }
    base.update(overrides)
    return make_settings(**base)


def test_fake_selection_keeps_day_015_registry() -> None:
    registry = build_registry(_provider_settings(llm_default_provider="fake"))
    assert registry.default_key == "fake"
    assert isinstance(registry.resolve("fake.http_429"), object)


def test_production_registry_is_dark_even_with_real_selection() -> None:
    registry = build_registry(
        _provider_settings(environment="production", llm_default_provider="openai"),
        credential_resolver=_AlwaysResolver(),
        adapter_client=_FakeClient(),
    )
    assert registry.names == ()
    with pytest.raises(NoProviderConfiguredError):
        registry.resolve(None)


def test_live_wiring_requires_credentials_and_cap() -> None:
    settings = _provider_settings(llm_default_provider="openai", llm_spend_cap_usd=0.05)
    # Missing credential ⇒ selection never becomes the default (still fake).
    dark = build_registry(
        settings, credential_resolver=_MissingResolver(), adapter_client=_FakeClient()
    )
    assert dark.default_key == "fake"
    # Zero spend cap ⇒ live wiring refused even with credentials.
    uncapped = _provider_settings(llm_default_provider="openai", llm_spend_cap_usd=0.0)
    capped = build_registry(
        uncapped, credential_resolver=_AlwaysResolver(), adapter_client=_FakeClient()
    )
    assert capped.default_key == "fake"
    # Both gates pass ⇒ openai is the default and fakes remain for conformance.
    wired = build_registry(
        settings, credential_resolver=_AlwaysResolver(), adapter_client=_FakeClient()
    )
    assert wired.default_key == "openai"
    assert "fake.http_429" in wired.names


def test_mock_selection_wires_without_credentials() -> None:
    client = _FakeClient()
    registry = build_registry(
        _provider_settings(llm_default_provider="anthropic", llm_litellm_mock=True),
        adapter_client=client,
    )
    assert registry.default_key == "anthropic"
    assert registry.resolve(None).capabilities["streaming"] is False  # type: ignore[attr-defined]


def test_local_selection_wires_openai_compatible_endpoint() -> None:
    registry = build_registry(
        _provider_settings(
            llm_default_provider="local",
            llm_local_base_url="http://127.0.0.1:11434/v1",
            llm_local_model="qwen2.5",
            llm_litellm_mock=True,
        ),
        adapter_client=_FakeClient(),
    )
    assert registry.default_key == "openai"  # LiteLLM addressing identity
    with pytest.raises(ValueError, match="loopback"):
        _provider_settings(llm_local_base_url="http://10.0.0.5:11434/v1")
    with pytest.raises(ValueError):
        _provider_settings(llm_local_base_url="https://user:pw@models.example/v1")


def test_build_gateway_consumes_the_gated_registry() -> None:
    gateway = build_gateway(
        _provider_settings(
            llm_default_provider="openai",
            llm_litellm_mock=True,
        )
    )
    assert gateway.registry.default_key == "openai"


async def test_mock_mode_through_gateway_completes_without_network() -> None:
    """LLM_LITELLM_MOCK=true end-to-end: the *real* code path, SDK-native canned
    answer, zero network and zero spend — acceptance check 1's proof."""
    client = _FakeClient(response=_ok_response(content="mocked deterministic completion"))
    registry = build_registry(
        _provider_settings(llm_default_provider="openai", llm_litellm_mock=True),
        adapter_client=client,
    )
    gateway = ChatGateway(registry, default_timeout_ms=5_000, max_timeout_ms=30_000)
    result = await gateway.complete(
        messages=[ChatMessageView(role="user", content="ping")],
        max_output_tokens=64,
        trace_id="t-1",
    )
    assert result.provider == "openai"
    assert result.content == "mocked deterministic completion"
    assert result.usage.kind is UsageKind.EXACT
    assert client.last["mock_response"] == "mocked deterministic completion"
