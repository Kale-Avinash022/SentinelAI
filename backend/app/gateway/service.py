"""The chat gateway service — the route's only entry to a provider (Day 015).

:func:`ChatGateway.complete` is the seam between HTTP and the provider port. It
holds no vendor knowledge: it resolves a provider from the registry, builds the
normalized :class:`~app.gateway.contracts.CompletionRequest`, runs the adapter
call under a hard deadline, and stamps the *measured* wall-clock latency onto
the result. Because the route talks to this service and never to an adapter (or
SDK) directly, the same path serves the fakes today and LiteLLM from Day 016.

Two normalization duties live here and nowhere else:

* **The timeout is the gateway's, not the provider's.** ``asyncio.wait_for``
  enforces ``timeout_ms``; a provider that overruns becomes a typed
  :class:`~app.gateway.contracts.ProviderTimeoutError` (PROVIDER_TIMEOUT, 504,
  retryable) regardless of what the adapter would eventually have returned. The
  ambiguous-billing consequence is Day 017's ledger concern; here we only prove
  the caller gets a clean, standard failure.
* **Latency is measured, never taken on faith.** ``result.latency_ms`` from an
  adapter is treated as an untrusted hint and overwritten with the real elapsed
  time, so cross-provider latency numbers mean the same thing.

Any unexpected adapter exception is reclassified as
:class:`~app.gateway.contracts.InternalProviderError` so a vendor's raw error
(or traceback text) can never ride the message into the client envelope.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import replace

from app.core.settings import Settings
from app.gateway.contracts import (
    AUTO_MODEL,
    ChatMessageView,
    CompletionRequest,
    InternalProviderError,
    ProviderCompletion,
    ProviderFailure,
    ProviderTimeoutError,
)
from app.gateway.fakes import FakeScenario
from app.gateway.registry import (
    DEFAULT_PROVIDER_KEY,
    ProviderRegistry,
    build_fake_registry,
)


class ChatGateway:
    """Provider-agnostic chat orchestrator bound to one registry."""

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        default_timeout_ms: int,
        max_timeout_ms: int,
    ) -> None:
        self._registry = registry
        self._default_timeout_ms = default_timeout_ms
        self._max_timeout_ms = max_timeout_ms

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    async def complete(
        self,
        *,
        messages: Sequence[ChatMessageView],
        model: str = AUTO_MODEL,
        max_output_tokens: int,
        timeout_ms: int | None = None,
        trace_id: str | None = None,
        options: dict[str, object] | None = None,
        provider_key: str | None = None,
    ) -> ProviderCompletion:
        """Resolve → request → timed adapter call → normalized result."""
        provider = self._registry.resolve(provider_key or DEFAULT_PROVIDER_KEY)
        effective_timeout = self._clamp_timeout(timeout_ms)
        request = CompletionRequest(
            provider=provider.name,
            # ``sentinel-auto`` (and blanks) resolve to the adapter's identity;
            # an explicitly pinned model is echoed through untouched.
            model=model if model and model != AUTO_MODEL else provider.default_model,
            messages=tuple(messages),
            max_output_tokens=max_output_tokens,
            timeout_ms=effective_timeout,
            trace_id=trace_id,
            options=dict(options or {}),
        )
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                provider.complete(request), timeout=effective_timeout / 1000.0
            )
        except TimeoutError as exc:
            # Builtin TimeoutError is asyncio.wait_for's timeout since 3.11.
            raise ProviderTimeoutError(
                "Provider did not respond within the gateway deadline."
            ) from exc
        except ProviderFailure:
            # Already a normalized, safe failure — pass it through untouched.
            raise
        except Exception as exc:  # noqa: BLE001 - adapter boundary: classify, never leak
            # A raw vendor/SDK error must never reach the client envelope; the
            # concrete type name is all we log, the message is generic.
            raise InternalProviderError("Provider failed to produce a completion.") from exc
        # Trust the measured clock over any adapter-reported latency hint.
        measured_ms = int((time.monotonic() - started) * 1000)
        return replace(result, latency_ms=measured_ms, trace_id=request.trace_id)

    def _clamp_timeout(self, timeout_ms: int | None) -> int:
        if timeout_ms is None:
            return self._default_timeout_ms
        return max(1, min(timeout_ms, self._max_timeout_ms))


def build_gateway(settings: Settings) -> ChatGateway:
    """Construct the process gateway from validated settings.

    Local/test seed the seven deterministic fakes and honour
    ``settings.llm_fake_scenario`` as the default behaviour. Production builds
    an empty registry so the gateway fails closed until a real adapter is
    wired (Day 016) — a fake never answers production traffic.
    """
    if settings.environment == "production":
        registry = ProviderRegistry()  # dark: resolve() raises 503
    else:
        registry = build_fake_registry(FakeScenario(settings.llm_fake_scenario))
    return ChatGateway(
        registry,
        default_timeout_ms=settings.llm_default_timeout_ms,
        max_timeout_ms=settings.llm_max_timeout_ms,
    )


__all__ = ["ChatGateway", "build_gateway"]
