"""Deterministic fake provider adapters (Day 015).

Seven repeatable scenarios behind the :class:`~app.gateway.contracts.ChatProvider`
port, so the chat contract can be tested end-to-end with **zero network
access and zero spend** — and so Day 016+ can prove a real LiteLLM adapter
against the same expectations.

Selection is by fixture/config, never by random global state: each fake is
constructed with a fixed :class:`FakeBehavior`, and the scenario is chosen when
the registry is built. Two consequences are pinned by tests:

* **Pure determinism.** Same behavior config + same request ⇒ same normalized
  result (content, usage, reported identity). Nothing reads a clock, an RNG or
  the environment mid-call.
* **Honest latency.** The ``latency`` scenario *parks* deterministically via an
  injectable sleep (real ``asyncio.sleep`` in local/dev, a scripted sleeper in
  tests) but the resulting ``latency_ms`` is measured by the gateway, never
  claimed by the fake — a provider that reports its own slowness is not a
  latency test.

Malformed and empty scenarios model the two ways real vendors corrupt a
"successful" HTTP 200: a body that cannot be normalized, and a completion with
no content. Both must become typed :class:`~app.gateway.contracts.ProviderFailure`
values, because downstream code is only safe if success always means
"a usable completion".
"""

from __future__ import annotations

import asyncio
import enum
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.gateway.contracts import (
    FAKE_MODEL,
    FAKE_PROVIDER,
    CompletionRequest,
    InternalProviderError,
    ProviderCompletion,
    ProviderUnavailableError,
    ProviderUsage,
    RateLimitedProviderError,
    UsageKind,
)

#: Signature of the injectable sleeper: awaits, optionally long, per call.
SleepFn = Callable[[float], Awaitable[None]]


class FakeScenario(enum.StrEnum):
    """The seven selectable fake behaviours from the step brief."""

    SUCCESS = "success"
    LATENCY = "latency"
    RATE_LIMIT_429 = "http_429"
    SERVER_ERROR_500 = "http_500"
    MALFORMED = "malformed"
    EMPTY = "empty"
    RESET = "reset"


#: How long the latency scenario parks, in milliseconds (deterministic).
DEFAULT_FAKE_LATENCY_MS = 2_000


@dataclass(frozen=True, slots=True)
class FakeBehavior:
    """Everything a fake may vary, fixed at construction.

    ``reply`` and the token counts are part of the *fixture*, not derived from
    the request, so two runs of the same scenario are byte-identical. The
    counts are reported as ``exact`` usage because a fake, unlike a real
    vendor, can always count its own deterministic output honestly.
    """

    scenario: FakeScenario = FakeScenario.SUCCESS
    reply: str = "SentinelAI deterministic fake completion."
    input_tokens: int = 128
    output_tokens: int = 32
    latency_ms: int = DEFAULT_FAKE_LATENCY_MS
    retry_after_seconds: int = 2


class DeterministicFakeProvider:
    """One fake adapter pinned to one :class:`FakeBehavior`.

    Satisfies :class:`~app.gateway.contracts.ChatProvider` structurally; there
    is no registration magic, so Day 016 adapters land in the same registry.
    """

    def __init__(self, behavior: FakeBehavior, *, sleep: SleepFn | None = None) -> None:
        self._behavior = behavior
        # Injectable clock-parking: tests pass a no-op sleeper; local/dev runs
        # the real asyncio.sleep so the gateway deadline is genuinely exercised.
        self._sleep = sleep if sleep is not None else asyncio.sleep

    @property
    def behavior(self) -> FakeBehavior:
        return self._behavior

    @property
    def name(self) -> str:
        return FAKE_PROVIDER

    @property
    def default_model(self) -> str:
        return FAKE_MODEL

    async def complete(self, request: CompletionRequest) -> ProviderCompletion:
        """Run the pinned scenario; see module docstring for each mapping."""
        behavior = self._behavior
        if behavior.scenario is FakeScenario.LATENCY:
            await self._sleep(behavior.latency_ms / 1000.0)

        match behavior.scenario:
            case FakeScenario.SUCCESS | FakeScenario.LATENCY:
                return self._success(request)
            case FakeScenario.RATE_LIMIT_429:
                # A vendor 429: retry later, with the advertised back-off.
                raise RateLimitedProviderError(
                    "Provider is rate limiting this tenant.",
                    retry_after_seconds=behavior.retry_after_seconds,
                )
            case FakeScenario.SERVER_ERROR_500:
                # A vendor 5xx on a *reachable* provider: internal failure.
                raise InternalProviderError("Provider returned a server error.")
            case FakeScenario.MALFORMED:
                # HTTP 200 with a body that cannot be normalized at all.
                raise InternalProviderError("Provider response could not be normalized.")
            case FakeScenario.EMPTY:
                # HTTP 200, well-formed, but no usable content.
                raise InternalProviderError("Provider returned an empty completion.")
            case FakeScenario.RESET:
                # Connection dropped before any answer: outage-class failure.
                raise ProviderUnavailableError("Provider connection was reset.")
            case _:  # pragma: no cover - enum is closed; guard for future adds
                raise InternalProviderError("Unknown fake scenario is not selectable.")

    def _success(self, request: CompletionRequest) -> ProviderCompletion:
        return ProviderCompletion(
            content=self._behavior.reply,
            provider=FAKE_PROVIDER,
            # Echo a pinned model when the caller asked for one, so identity
            # normalization is visible in the contract; else the fake's own.
            model=request.model or FAKE_MODEL,
            usage=ProviderUsage(
                kind=UsageKind.EXACT,
                input_tokens=self._behavior.input_tokens,
                output_tokens=self._behavior.output_tokens,
            ),
            # A fake does not actually take time; the gateway measures real
            # elapsed latency around this call regardless of this hint.
            latency_ms=0,
            trace_id=request.trace_id,
            finish_reason="stop",
        )


__all__ = [
    "DEFAULT_FAKE_LATENCY_MS",
    "DeterministicFakeProvider",
    "FakeBehavior",
    "FakeScenario",
    "SleepFn",
]
