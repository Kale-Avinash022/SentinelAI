"""The LiteLLM adapter — a real vendor behind the Day 015 port (Day 016).

One class wraps every LiteLLM-supported chat vendor, and its only job is
*translation in both directions*: normalized :class:`~app.gateway.contracts.
CompletionRequest` values in, vendor kwargs out; vendor responses and
exceptions back out as :class:`~app.gateway.contracts.ProviderCompletion` or a
typed :class:`~app.gateway.contracts.ProviderFailure`. Nothing above this file
ever sees a LiteLLM type, which is what lets the same contract tests that pin
the fakes pin real providers too (``test_provider_conformance.py``).

The four duties that make a vendor trustworthy:

* **Closed destination, always.** Every call is re-checked against the
  :class:`~app.gateway.allowlist.DestinationAllowlist` — including the model a
  caller pinned in the request — so this adapter can never be steered to an
  arbitrary URL (SSRF guard), and credentials arrive only through the secret
  adapter reference, never through request bodies.
* **Honest usage labels.** Counts reported by the provider are ``exact``;
  when a vendor omits them the adapter derives a deliberate characters/4
  heuristic and labels it ``estimated`` — never laundering a guess as a bill.
* **Spend cap before the call.** A configured per-call USD cap is checked
  against the *planned* maximum output *before* anything is sent, so an
  accidental fortune is refused locally rather than invoiced afterwards.
* **Streaming is refused, not faked.** The port is synchronous-completion
  shaped (streaming lands Day 020), so ``stream=True`` would be an
  unsupported capability pretending to work; adapters report capabilities
  honestly instead.

``import litellm`` happens lazily inside the call path: unit tests replace the
client seam with a mock, default CI never imports the SDK, and a deployment
that only uses fakes never pays the import cost.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import SecretStr

from app.gateway.allowlist import (
    DestinationAllowlist,
    DestinationNotAllowedError,
    normalize_base_url,
)
from app.gateway.contracts import (
    PROVIDER_ERROR_CLASSES,
    CompletionRequest,
    ForbiddenProviderError,
    GatewayErrorCode,
    InternalProviderError,
    InvalidRequestProviderError,
    ProviderCompletion,
    ProviderFailure,
    ProviderUnavailableError,
    ProviderUsage,
    RateLimitedProviderError,
    UnauthorizedProviderError,
    UsageKind,
)

#: Heuristic characters-per-token when a vendor omits usage entirely.
#: Deliberately crude — its only contract is to be labeled ``estimated``.
CHARS_PER_TOKEN_ESTIMATE = 4


def _load_litellm() -> Any:
    """Import the SDK lazily so fake-only environments never load it.

    Raises :class:`ProviderUnavailableError` (503, retryable) rather than a
    bare ``ImportError``: a missing dependency is an outage-class condition
    at the provider boundary, not the caller's fault.
    """
    try:
        import litellm
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise ProviderUnavailableError("Provider SDK is not installed in this runtime.") from exc
    return litellm


def supports_streaming(provider: object) -> bool:
    """Capability probe used by conformance tests and (Day 020) routes.

    Duck-typed so the port itself stays capability-free for now: adapters that
    know they stream report ``True``; everything else is honestly ``False``.
    """
    return bool(getattr(provider, "capabilities", {}).get("streaming", False))


class LiteLLMAdapter:
    """ChatProvider port implementation backed by ``litellm.acompletion``."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        credential: SecretStr | None = None,
        base_url: str | None = None,
        allowlist: DestinationAllowlist,
        spend_cap_usd: float | None = None,
        price_per_mtok_output: float | None = None,
        mock_mode: bool = False,
        client: Any = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._credential = credential
        self._base_url = normalize_base_url(base_url) if base_url else None
        self._allowlist = allowlist
        self._spend_cap_usd = spend_cap_usd
        self._price_per_mtok_output = price_per_mtok_output
        # mock_mode routes through litellm's own mock layer: same code path,
        # zero network and zero spend. Used by local "LITELLM_MOCK=true" runs.
        self._mock_mode = mock_mode
        # Injectable SDK seam: tests pass a stub; production passes nothing
        # and the module imports litellm once, lazily, on first use.
        self._client = client
        # The vendor-official default endpoint is allowlisted implicitly; a
        # configured override must already appear in the allowlist.
        self._allowlist.require(provider, model, self._base_url)

    @property
    def name(self) -> str:
        return self._provider

    @property
    def default_model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> dict[str, bool]:
        """Honest capability report (streaming arrives with Day 020)."""
        return {"streaming": False, "usage_counting": True}

    async def complete(self, request: CompletionRequest) -> ProviderCompletion:
        """Translate one normalized request into one vendor call, faithfully."""
        if request.options.get("stream"):
            raise InvalidRequestProviderError(
                "Streaming is not a supported capability of this gateway yet."
            )
        # The caller's pin is validated here, not upstream: this is the last
        # seam before money can move. A non-allowlisted destination is a
        # typed FORBIDDEN — never the ValueError it is at build time.
        try:
            self._allowlist.require(self._provider, request.model, self._base_url)
        except DestinationNotAllowedError as exc:
            raise ForbiddenProviderError(str(exc)) from exc
        self._check_spend_cap(request)

        client = self._client if self._client is not None else _load_litellm()
        kwargs: dict[str, Any] = {
            # LiteLLM's own "<provider>/<model>" addressing; a bare model for
            # custom endpoints (local) is passed through untouched.
            "model": f"{self._provider}/{self._model}" if self._base_url is None else self._model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "max_tokens": request.max_output_tokens,
            "timeout": request.timeout_ms / 1000.0,
        }
        for key in ("temperature", "top_p"):
            if request.options.get(key) is not None:
                kwargs[key] = request.options[key]
        if self._base_url is not None:
            kwargs["api_base"] = self._base_url
        if self._mock_mode:
            # SDK-native mock: exercises the real call path, returns a canned
            # ModelResponse, touches nothing external and bills nothing.
            kwargs["mock_response"] = "mocked deterministic completion"
        elif self._credential is not None:
            kwargs["api_key"] = self._credential.get_secret_value()

        started = time.monotonic()
        try:
            response = await client.acompletion(**kwargs)
        except ProviderFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - SDK boundary: translate all
            raise self._translate_exception(exc) from exc
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return self._normalize(response, request, elapsed_ms)

    # -- normalization ------------------------------------------------------

    def _normalize(
        self, response: Any, request: CompletionRequest, elapsed_ms: int
    ) -> ProviderCompletion:
        """Vendor dictionary → closed normalized types, or a typed failure."""
        choices = getattr(response, "choices", None)
        if not choices:
            raise InternalProviderError("Provider response could not be normalized.")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None) if message is not None else None
        if not isinstance(content, str) or not content:
            # An empty-but-well-formed answer is still unusable: same honest
            # classification the Day 015 ``empty`` fake demonstrates.
            raise InternalProviderError("Provider returned an empty completion.")
        finish_reason = getattr(choices[0], "finish_reason", None) or "stop"

        reported = getattr(response, "usage", None)
        input_tokens = getattr(reported, "prompt_tokens", None)
        output_tokens = getattr(reported, "completion_tokens", None)
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            usage = ProviderUsage(
                kind=UsageKind.EXACT, input_tokens=input_tokens, output_tokens=output_tokens
            )
        else:
            usage = self._estimate_usage(request, content)

        model = getattr(response, "model", None) or request.model or self._model
        return ProviderCompletion(
            content=content,
            provider=self._provider,
            # Vendor-reported identity wins (it knows about aliases/deployments)
            # but always with the allowlisted model as the honest fallback.
            model=str(model),
            usage=usage,
            latency_ms=elapsed_ms,
            trace_id=request.trace_id,
            finish_reason=str(finish_reason),
        )

    def _estimate_usage(self, request: CompletionRequest, content: str) -> ProviderUsage:
        """Characters/4 both ways, labeled so nobody mistakes it for a bill."""
        prompt_chars = sum(len(m.content) for m in request.messages)
        return ProviderUsage(
            kind=UsageKind.ESTIMATED,
            input_tokens=max(1, -(-prompt_chars // CHARS_PER_TOKEN_ESTIMATE)),
            output_tokens=max(1, -(-len(content) // CHARS_PER_TOKEN_ESTIMATE)),
        )

    def _check_spend_cap(self, request: CompletionRequest) -> None:
        """Refuse, before sending, a call whose planned output can't fit the cap."""
        cap = self._spend_cap_usd
        price = self._price_per_mtok_output
        if cap is None or price is None:
            return  # uncapped or unknown price: no honest bound to enforce yet
        worst_case_usd = (request.max_output_tokens / 1_000_000.0) * price
        if worst_case_usd > cap:
            raise ForbiddenProviderError(
                "Requested completion exceeds the configured per-call spend cap."
            )

    # -- vendor exception translation --------------------------------------

    def _translate_exception(self, exc: Exception) -> ProviderFailure:
        """Map any SDK-raised error onto exactly one typed ProviderFailure.

        Keyed first on LiteLLM's exception classes (the verified 1.110.2
        ladder: ``AuthenticationError``, ``PermissionDeniedError``,
        ``ContextWindowExceededError``, ``Timeout``, …; there is no
        ``APITimeoutError``/``Forbidden`` at the module top level, and every
        lookup is defensive), then on the numeric ``status_code`` attribute
        LiteLLM mirrors from the vendor HTTP layer, then on
        connection-shaped errors. Vendor text never travels: each failure
        carries one of the gateway's generic, operator-safe messages.
        """
        litellm = self._client if self._client is not None else _load_litellm()

        def _named(*names: str) -> type[BaseException] | None:
            for name in names:
                cls = getattr(litellm, name, None)
                if isinstance(cls, type) and issubclass(cls, BaseException):
                    return cls
            return None

        auth_cls = _named("AuthenticationError")
        timeout_cls = _named("Timeout", "APITimeoutError")
        conn_cls = _named("APIConnectionError", "InternalServerError")
        ratelimit_cls = _named("RateLimitError")
        forbidden_cls = _named("PermissionDeniedError", "Forbidden")
        bad_request_cls = _named(
            "ContextWindowExceededError",
            "ContentPolicyViolationError",
            "BadRequestError",
            "InvalidRequestError",
        )
        api_error_cls = _named("APIError")

        if auth_cls is not None and isinstance(exc, auth_cls):
            return UnauthorizedProviderError("Provider rejected the configured credentials.")
        if forbidden_cls is not None and isinstance(exc, forbidden_cls):
            return ForbiddenProviderError("Provider credentials lack access to this model.")
        if bad_request_cls is not None and isinstance(exc, bad_request_cls):
            return InvalidRequestProviderError("Provider rejected the request.")

        if timeout_cls is not None and isinstance(exc, timeout_cls):
            return _timeout_failure()
        if conn_cls is not None and isinstance(exc, conn_cls):
            return ProviderUnavailableError("Provider could be reached.")
        if ratelimit_cls is not None and isinstance(exc, ratelimit_cls):
            return RateLimitedProviderError(
                "Provider is rate limiting this deployment.",
                retry_after_seconds=_retry_after_seconds(exc),
            )
        if api_error_cls is not None and isinstance(exc, api_error_cls):
            return _from_status_code(exc)

        # Class-name and status-code fallback for anything not in the ladder
        # (custom endpoints, wrapped httpx errors, future SDK renames).
        name = type(exc).__name__.lower()
        if "timeout" in name:
            return _timeout_failure()
        if "connection" in name or "reset" in name or "unreachable" in name:
            return ProviderUnavailableError("Provider connection failed.")
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            return _from_status_code(exc)
        # Unknown but destination-shaped errors are *our* boundary failure,
        # never the caller's; INTERNAL_ERROR is the honest, leak-free label.
        return InternalProviderError("Provider failed to produce a completion.")


def _timeout_failure() -> ProviderFailure:
    return PROVIDER_ERROR_CLASSES[GatewayErrorCode.PROVIDER_TIMEOUT](
        "Provider exceeded its own deadline before answering."
    )


def _from_status_code(exc: Exception) -> ProviderFailure:
    code = getattr(exc, "status_code", None)
    if code == 401:
        return UnauthorizedProviderError("Provider rejected the configured credentials.")
    if code == 403:
        return ForbiddenProviderError("Provider credentials lack access to this model.")
    if code == 400 or code == 404 or code == 422:
        return InvalidRequestProviderError("Provider rejected the request.")
    if code == 429:
        return RateLimitedProviderError(
            "Provider is rate limiting this deployment.",
            retry_after_seconds=_retry_after_seconds(exc),
        )
    if code == 503:
        return ProviderUnavailableError("Provider is currently unavailable.")
    return InternalProviderError("Provider returned a server error.")


def _retry_after_seconds(exc: Exception) -> int | None:
    """Pull a back-off hint from LiteLLM's attribute or the vendor headers."""
    candidate = getattr(exc, "retry_after", None)
    if isinstance(candidate, int | float) and candidate >= 0:
        return max(0, int(candidate))
    headers = getattr(exc, "headers", None)
    if headers:
        try:
            raw = headers.get("retry-after") or headers.get("x-ratelimit-reset-requests")
        except (AttributeError, TypeError):
            return None
        if raw is not None:
            try:
                return max(0, int(float(raw)))
            except (TypeError, ValueError):
                return None
    return None


__all__ = ["CHARS_PER_TOKEN_ESTIMATE", "LiteLLMAdapter", "supports_streaming"]
