"""Provider registry: fixture/config-seeded selection (Days 015–016).

Maps a stable provider *key* to a :class:`~app.gateway.contracts.ChatProvider`
and picks the default for ``sentinel-auto``. Nothing here is chosen per request
by an untrusted caller: the set of registered providers and the default key are
fixed when the gateway is built, which is the "controls only in local/test
settings, never random global state" rule from the step.

In local/test the registry is seeded with all seven deterministic fakes under
``fake.<scenario>`` plus a ``fake`` alias pointing at the settings-selected
default scenario. Day 016 adds :func:`build_registry`: when settings select a
real provider (``local``/``openai``/``anthropic``) *and* its destination is
allowlisted, its credential reference resolves, and a live spend cap is
configured, the adapter becomes the default and ``sentinel-auto`` follows it
(the fakes stay registered as ``fake.*`` fixtures for conformance tests).
Production keeps the Day 015 rule: nothing is wired by configuration alone,
the registry stays dark, and ``resolve()`` answers 503 until a deployment
explicitly completes the real-provider wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import DependencyUnavailableError
from app.core.settings import Settings
from app.gateway.allowlist import (
    DestinationNotAllowedError,
    ProviderDestination,
    build_allowlist,
)
from app.gateway.contracts import (
    FAKE_PROVIDER,
    ChatProvider,
)
from app.gateway.credentials import (
    CredentialNotConfiguredError,
    CredentialResolver,
    EnvironmentCredentialResolver,
)
from app.gateway.fakes import (
    DEFAULT_FAKE_LATENCY_MS,
    DeterministicFakeProvider,
    FakeBehavior,
    FakeScenario,
    SleepFn,
)

#: Registry key of the optional local OpenAI-compatible endpoint *selection*
#: (a settings value; its adapters register under their LiteLLM identity).
LOCAL_PROVIDER_KEY = "local"
#: Provider identities that are real vendors addressed through LiteLLM.
OPENAI_PROVIDER_KEY = "openai"
ANTHROPIC_PROVIDER_KEY = "anthropic"

#: Provider identity LiteLLM uses for arbitrary OpenAI-compatible endpoints.
_LITELLM_OPENAI_COMPAT_PREFIX = "openai"


#: Historical name for "the registry's built default" (Day 015). From Day 016
#: the default is a build-time decision inside :func:`build_registry`, so this
#: constant only documents the fake-only default of fixture-built registries.
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


def _parse_price_map(raw: str) -> dict[str, float]:
    """Parse the validated ``provider=usd`` settings string into a mapping."""
    prices: dict[str, float] = {}
    for pair in raw.split(","):
        name, sep, price = pair.partition("=")
        if sep and name.strip() and price.strip():
            prices[name.strip().lower()] = float(price)
    return prices


def _real_destination(settings: Settings) -> ProviderDestination | None:
    """The (provider, model, base_url) triple the settings select, if any.

    ``fake`` (and anything else) has no real destination; ``local`` is always
    addressed LiteLLM-style as ``openai/<model>`` against its OpenAI-compatible
    base URL, so a plain ``/v1/chat/completions`` server speaks the same port.
    """
    choice = settings.llm_default_provider
    if choice == LOCAL_PROVIDER_KEY:
        if not settings.llm_local_base_url:
            return None
        return ProviderDestination(
            provider=_LITELLM_OPENAI_COMPAT_PREFIX,
            model=settings.llm_local_model,
            base_url=settings.llm_local_base_url,
        )
    if choice == OPENAI_PROVIDER_KEY:
        return ProviderDestination(provider=OPENAI_PROVIDER_KEY, model=settings.llm_openai_model)
    if choice == ANTHROPIC_PROVIDER_KEY:
        return ProviderDestination(
            provider=ANTHROPIC_PROVIDER_KEY, model=settings.llm_anthropic_model
        )
    return None


def build_registry(
    settings: Settings,
    *,
    credential_resolver: CredentialResolver | None = None,
    sleep: SleepFn | None = None,
    adapter_client: object = None,
) -> ProviderRegistry:
    """Build the process registry from validated settings — the Day 016 gate.

    Order of decisions (each failure leaves the gateway *dark*, never partly
    wired and never fake-serving-real traffic):

    1. Production registers nothing at all (the Day 015 fail-closed rule).
    2. ``fake`` default ⇒ the Day 015 fake-only registry.
    3. A real selection must name an allowlisted destination, resolve its
       credential reference (except unauthenticated local/mock runs), and
       carry a non-zero live spend cap; only then does its adapter become
       the default, with all fakes still registered under ``fake.*`` for
       conformance coverage.

    ``credential_resolver`` and ``adapter_client`` are injectable seams: tests
    hand in fakes so no environment variable or SDK is ever touched.
    """
    if settings.environment == "production":
        return ProviderRegistry()  # dark: resolve() raises 503

    default_scenario = FakeScenario(settings.llm_fake_scenario)
    destination = _real_destination(settings)
    if destination is None:
        return build_fake_registry(default_scenario, sleep=sleep)

    registry = ProviderRegistry()
    for scenario in FakeScenario:
        behavior = FakeBehavior(scenario=scenario, latency_ms=DEFAULT_FAKE_LATENCY_MS)
        registry.register(
            fake_scenario_key(scenario), DeterministicFakeProvider(behavior, sleep=sleep)
        )
    # The fake alias starts as the default; a successfully gated real adapter
    # takes over below. If live wiring is refused (missing credential, zero
    # cap, unallowlisted destination), sentinel-auto honestly degrades to the
    # deterministic fake instead of silently going dark.
    registry.register(
        FAKE_PROVIDER,
        DeterministicFakeProvider(FakeBehavior(scenario=default_scenario), sleep=sleep),
        make_default=True,
    )

    mock = settings.llm_litellm_mock
    # Live calls need a real cap; 0 (or a missing SDK install) refuses wiring.
    if not mock and settings.llm_spend_cap_usd <= 0:
        return registry  # sentinel-auto stays on the honest fake default

    from app.gateway.adapters.litellm_adapter import LiteLLMAdapter

    # Credentials resolve only for live vendor calls, only through the secret
    # adapter, and only by reference — a local endpoint (loopback, no auth)
    # and LiteLLM-mocked runs legitimately need none.
    credential = None
    live_ref = {
        OPENAI_PROVIDER_KEY: settings.llm_openai_credential_ref,
        ANTHROPIC_PROVIDER_KEY: settings.llm_anthropic_credential_ref,
    }.get(destination.provider)
    if not mock and live_ref is not None:
        resolver = credential_resolver or EnvironmentCredentialResolver()
        try:
            credential = resolver.resolve(live_ref)
        except CredentialNotConfiguredError:
            # Missing credentials are not an excuse to serve fakes as real:
            # the selection simply never becomes the default.
            return registry

    try:
        allowlist_entries = [f"{destination.provider}/{destination.model}"]
        if destination.base_url is not None:
            allowlist_entries[0] += f"@{destination.base_url}"
        allowlist_entries.extend(
            entry for entry in settings.llm_allowed_destinations.split(",") if entry.strip()
        )
        allowlist = build_allowlist(allowlist_entries)
        # A local OpenAI-compatible endpoint is priced like OpenAI addressing.
        price_key = (
            OPENAI_PROVIDER_KEY
            if destination.provider == _LITELLM_OPENAI_COMPAT_PREFIX
            else destination.provider
        )
        prices = _parse_price_map(settings.llm_price_per_mtok_output)
        adapter = LiteLLMAdapter(
            provider=destination.provider,
            model=destination.model,
            credential=credential,
            base_url=destination.base_url,
            allowlist=allowlist,
            spend_cap_usd=settings.llm_spend_cap_usd or None,
            price_per_mtok_output=prices.get(price_key),
            mock_mode=mock,
            client=adapter_client,
        )
    except (DestinationNotAllowedError, ValueError):
        return registry
    registry.register(destination.provider, adapter, make_default=True)
    return registry


__all__ = [
    "ANTHROPIC_PROVIDER_KEY",
    "DEFAULT_PROVIDER_KEY",
    "LOCAL_PROVIDER_KEY",
    "NoProviderConfiguredError",
    "OPENAI_PROVIDER_KEY",
    "ProviderRegistry",
    "build_allowlist",
    "build_fake_registry",
    "build_registry",
    "fake_scenario_key",
]
