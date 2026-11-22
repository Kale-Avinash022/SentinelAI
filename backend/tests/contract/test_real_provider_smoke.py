"""Opt-in live-provider smoke test (Day 016) — never part of default CI.

This is the only file in the suite allowed to reach a real vendor, and even
then only under *explicit human approval*: it is marked ``real_provider``
(excluded by the documented ``-m "not real_provider"`` default run and by CI)
**and** skips unless ``SENTINEL_ALLOW_REAL_SMOKE=1`` is set in the
environment, **and** requires a real credential reference to resolve. One
tiny prompt, one small allowlisted model, a hard per-call spend cap checked
before the request goes out — the same guardrails the fakes proved, now
proven to actually gate money.

Run deliberately (and only after approving the spend)::

    SENTINEL_ALLOW_REAL_SMOKE=1 OPENAI_API_KEY=sk-... \
      .venv/Scripts/python -m pytest backend/tests/contract/test_real_provider_smoke.py -m real_provider -q
"""

from __future__ import annotations

import os

import pytest
from app.gateway.contracts import ChatMessageView, UsageKind
from app.gateway.credentials import (
    CredentialNotConfiguredError,
    EnvironmentCredentialResolver,
)
from app.gateway.registry import build_registry
from app.gateway.service import ChatGateway

from tests.conftest import TEST_SECRET_KEY, make_settings

pytestmark = pytest.mark.real_provider


async def test_one_tiny_live_completion_under_the_cap() -> None:
    if os.environ.get("SENTINEL_ALLOW_REAL_SMOKE") != "1":
        pytest.skip("live vendor calls require SENTINEL_ALLOW_REAL_SMOKE=1 (explicit approval)")

    resolver = EnvironmentCredentialResolver()
    try:
        resolver.resolve("OPENAI_API_KEY")
    except CredentialNotConfiguredError:
        pytest.skip("OPENAI_API_KEY reference not present — nothing to spend against")

    settings = make_settings(
        environment="development",
        secret_key=TEST_SECRET_KEY,
        database_url="",
        redis_url="",
        llm_default_provider="openai",
        llm_openai_model="gpt-4o-mini",
        llm_spend_cap_usd=0.01,  # hard pre-call cap: the smoke cannot invoice more
    )
    # Deliberately no adapter_client: this one real call uses the real SDK.
    registry = build_registry(settings, credential_resolver=resolver)
    assert registry.default_key == "openai", "gate refused to wire the live provider"
    gateway = ChatGateway(registry, default_timeout_ms=20_000, max_timeout_ms=30_000)

    result = await gateway.complete(
        messages=[ChatMessageView(role="user", content="Reply with the single word: pong")],
        max_output_tokens=8,
        trace_id="real-smoke",
    )
    assert result.provider == "openai"
    assert result.content.strip()
    # Live vendors count their own tokens; an estimate here would mean the
    # normalization silently dropped the bill, which is exactly what Day 017
    # forbids. Latency and trace ride back on the normalized result.
    assert result.usage.kind is UsageKind.EXACT
    assert result.trace_id == "real-smoke"
    assert result.latency_ms >= 0
