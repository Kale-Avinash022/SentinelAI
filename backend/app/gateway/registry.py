"""Provider registry: fixture/config-seeded selection (Day 015).

Maps a stable provider *key* to a :class:`~app.gateway.contracts.ChatProvider`
and picks the default for ``sentinel-auto``. Nothing here is chosen per request
by an untrusted caller: the set of registered providers and the default key are
fixed when the gateway is built, which is the "controls only in local/test
settings, never random global state" rule from the step.

In local/test the registry is seeded with all seven deterministic fakes under
``fake.<scenario>`` plus a ``fake`` alias pointing at the settings-selected
default scenario. In production no fake is registered and the default is unset,
so the chat gateway fails closed until a real adapter is wired (Day 016) — a
fake can never serve production traffic by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import DependencyUnavailableError
from app.gateway.contracts import FAKE_PROVIDER, ChatProvider
from app.gateway.fakes import (
    DEFAULT_FAKE_LATENCY_MS,
    DeterministicFakeProvider,
    FakeBehavior,
    FakeScenario,
    SleepFn,
)

#: Provider key that ``sentinel-auto`` and an unset request both resolve to.
DEFAULT_PROVIDER_KEY = FAKE_PROVIDER


class NoProviderConfiguredError(DependencyUnavailableError):
    """No provider is registered for this environment — the gateway is dark.

    A subclass of the standard 503 outage error, so an unwired production
    gateway answers with the ordinary ``dependency_unavailable`` envelope
    rather than a bespoke shape or a fake silently standing in.
    """


@dataclass(slots=True)
class ProviderRegistry:
    """An immutable-by-convention name → provider map with one default key."""

    _providers: dict[str, ChatProvider] = field(default_factory=dict)
    _default_key: str | None = None

    def register(self, key: str, provider: ChatProvider, *, make_default: bool = False) -> None:
        self._providers[key] = provider
        if make_default:
            self._default_key = key

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    @property
    def default_key(self) -> str | None:
        return self._default_key

    def resolve(self, key: str | None = None) -> ChatProvider:
        """Look up ``key`` (or the default). Unknown/absent names never guess:
        an empty registry is an outage, a bad name is also refused."""
        chosen = key or self._default_key
        if chosen is None or chosen not in self._providers:
            raise NoProviderConfiguredError("No LLM provider is configured for this request.")
        return self._providers[chosen]


def fake_scenario_key(scenario: FakeScenario) -> str:
    """The registry key a scenario's fake is filed under (``fake.http_429``)."""
    return f"{FAKE_PROVIDER}.{scenario.value}"


def build_fake_registry(
    default_scenario: FakeScenario = FakeScenario.SUCCESS,
    *,
    sleep: SleepFn | None = None,
    behavior_overrides: dict[FakeScenario, FakeBehavior] | None = None,
) -> ProviderRegistry:
    """Register every deterministic fake once, then alias ``fake`` to the
    settings-selected default scenario.

    ``behavior_overrides`` lets a fixture pin a specific scenario's counts or
    latency (still by explicit config, not mutable globals). ``sleep`` is
    threaded into every fake so a test can drive the latency park without
    real wall-clock cost.
    """
    registry = ProviderRegistry()
    overrides = behavior_overrides or {}
    for scenario in FakeScenario:
        behavior = overrides.get(
            scenario,
            FakeBehavior(scenario=scenario, latency_ms=DEFAULT_FAKE_LATENCY_MS),
        )
        registry.register(
            fake_scenario_key(scenario), DeterministicFakeProvider(behavior, sleep=sleep)
        )
    # The bare ``fake`` alias is what ``sentinel-auto`` resolves to.
    registry.register(
        FAKE_PROVIDER,
        DeterministicFakeProvider(
            overrides.get(default_scenario, FakeBehavior(scenario=default_scenario)),
            sleep=sleep,
        ),
        make_default=True,
    )
    return registry


__all__ = [
    "DEFAULT_PROVIDER_KEY",
    "NoProviderConfiguredError",
    "ProviderRegistry",
    "build_fake_registry",
    "fake_scenario_key",
]
