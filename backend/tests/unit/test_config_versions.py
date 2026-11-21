"""Day 014 unit tests: payload rules, sealing, redaction and content addressing.

Everything here is hermetic — no database, no HTTP, no clock — because the four
acceptance checks ("versions are immutable", "activation is compare-and-swap",
"diffs redact", "last-known-good needs evidence") rest on pure rules that must
be pinned before a single row exists. The database-backed half of the proof
lives in ``tests/security/test_config_versions.py``.

Note on ``assert`` usage: Bandit's B101 excludes tests deliberately, and these
assertions are the specification, not runtime validation.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.auth.keys import API_KEY_PREFIX
from app.configs.rules import (
    MAX_BODY_CHARS,
    PAYLOAD_SCHEMAS,
    REDACTED_MARKER,
    ConfigKind,
    DiffEntry,
    PayloadSemanticsError,
    PromptPayload,
    ProviderRoutePayload,
    canonical_json,
    content_hash,
    diff_body,
    diff_payloads,
    fingerprint,
    is_sensitive_name,
    parse_payload,
    redact,
    validate_slot_name,
    validate_version_pairing,
)
from app.configs.vault import FernetVault, VaultError, is_sealed
from app.services.audit import (
    FORBIDDEN_DETAIL_MARKERS,
    AuditAction,
    AuditResource,
    serialize_detail,
)
from app.services.config_versions import _reject_secret_material, _typed_kind
from pydantic import ValidationError

MASTER_SECRET = "unit-test-master-secret-0123456789-abcdef"
OTHER_SECRET = "another-unit-test-master-secret-0123456789"

PROMPT_BODY = "You are a triage assistant for {{customer_name}}. Ticket: {{ticket_summary}}"


def route_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "primary_provider": "openai",
        "allowed_providers": ["openai", "anthropic"],
        "model_name": "gpt-4o-mini",
        "capabilities": {"streaming": True, "tools": False, "vision": False},
        "privacy_class": "internal",
        "quality_floor": 0.8,
        "timeout_ms": 5000,
        "max_input_tokens": 8000,
        "monthly_budget_usd": 500.0,
        "evaluator_version": "eval-v2",
    }
    base.update(overrides)
    return base


def prompt_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "provider": "openai",
        "model_name": "gpt-4o-mini",
        "variables": ["customer_name", "ticket_summary"],
        "output_format": "text",
        "max_output_tokens": 800,
        "quality_floor": 0.7,
        "evaluator_version": "eval-v2",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Encrypted payload adapter
# ---------------------------------------------------------------------------


def test_seal_then_open_round_trips_and_hides_the_body() -> None:
    vault = FernetVault(MASTER_SECRET)
    sealed = vault.seal(PROMPT_BODY)
    assert is_sealed(sealed)
    assert PROMPT_BODY not in sealed
    assert "customer_name" not in sealed
    assert sealed.startswith("fernet:v1:")
    assert vault.open(sealed) == PROMPT_BODY


def test_sealing_the_same_body_twice_yields_different_ciphertext() -> None:
    """Fernet randomizes the IV/timestamp, which is why version identity is
    proven by :func:`content_hash` over the plaintext, never by ciphertext."""
    vault = FernetVault(MASTER_SECRET)
    assert vault.seal(PROMPT_BODY) != vault.seal(PROMPT_BODY)
    assert vault.open(vault.seal(PROMPT_BODY)) == PROMPT_BODY


def test_open_fails_closed_on_tampering_and_on_a_foreign_key() -> None:
    vault = FernetVault(MASTER_SECRET)
    sealed = vault.seal(PROMPT_BODY)
    tampered = sealed[:-6] + ("A" * 5 + sealed[-1])
    with pytest.raises(VaultError):
        vault.open(tampered)
    with pytest.raises(VaultError):
        FernetVault(OTHER_SECRET).open(sealed)


def test_open_rejects_unsealed_input_and_unknown_key_versions() -> None:
    vault = FernetVault(MASTER_SECRET)
    with pytest.raises(VaultError):
        vault.open("plain text was never sealed")
    with pytest.raises(VaultError):
        vault.open("fernet:v9:AAAA")
    # The error text is opaque by design: no cryptography internals leak out.
    with pytest.raises(VaultError, match="recognized sealed format"):
        vault.open("fernet:notanumber:x")


def test_vault_construction_validates_its_inputs() -> None:
    with pytest.raises(ValueError, match="non-empty master secret"):
        FernetVault("")
    with pytest.raises(ValueError, match="key_version must be positive"):
        FernetVault(MASTER_SECRET, key_version=0)
    assert FernetVault(MASTER_SECRET, key_version=2).seal("x").startswith("fernet:v2:")


# ---------------------------------------------------------------------------
# Closed, typed metadata schemas
# ---------------------------------------------------------------------------


def test_every_kind_has_a_schema_and_unknown_kinds_are_unusable() -> None:
    assert set(PAYLOAD_SCHEMAS) == {kind.value for kind in ConfigKind}
    assert isinstance(
        parse_payload(ConfigKind.PROVIDER_ROUTE, route_payload()), ProviderRoutePayload
    )
    assert isinstance(parse_payload("prompt", prompt_payload()), PromptPayload)
    with pytest.raises(ValueError):
        parse_payload("shady_route", route_payload())
    assert _typed_kind("prompt") is ConfigKind.PROMPT
    with pytest.raises(Exception, match="Unknown configuration kind"):
        _typed_kind("anything_else")


def test_schemas_are_closed_unknown_fields_are_a_validation_error() -> None:
    """An unrecognised field must never become a silently-ignored capability."""
    with pytest.raises(ValidationError):
        parse_payload("provider_route", route_payload(api_key="sk-live-nope"))
    with pytest.raises(ValidationError):
        parse_payload("prompt", prompt_payload(credential="anything"))


def test_route_payload_enforces_value_rules() -> None:
    with pytest.raises(ValidationError, match="allowed_providers must be unique"):
        parse_payload("provider_route", route_payload(allowed_providers=["openai", "openai"]))
    with pytest.raises(ValidationError, match="blank entries"):
        parse_payload("provider_route", route_payload(allowed_providers=["openai", "  "]))
    with pytest.raises(ValidationError, match="prompt_version_id must be a UUID"):
        parse_payload("provider_route", route_payload(prompt_version_id="not-a-uuid"))
    with pytest.raises(ValidationError):
        parse_payload("provider_route", route_payload(quality_floor=1.5))
    with pytest.raises(ValidationError):
        parse_payload("provider_route", route_payload(timeout_ms=5))
    with pytest.raises(ValidationError):
        parse_payload("provider_route", route_payload(monthly_budget_usd=-1))
    with pytest.raises(ValidationError):
        parse_payload("provider_route", route_payload(privacy_class="plausible-deniability"))


def test_primary_provider_must_be_in_the_allowed_list() -> None:
    """A cross-field rule, so it lives in the pairing step rather than in a
    single-field validator (and the store re-checks it, not just the edge)."""
    payload = parse_payload("provider_route", route_payload(primary_provider="mistral"))
    with pytest.raises(PayloadSemanticsError, match="must be one of allowed_providers"):
        validate_version_pairing("provider_route", payload, body=None, secret_ref=None)


def test_prompt_reference_is_canonicalized_and_variables_are_bounded() -> None:
    reference = "080a1f3a-3f2e-4d18-9d1b-6c00a3b9e999"
    parsed = parse_payload("provider_route", route_payload(prompt_version_id=reference.upper()))
    assert isinstance(parsed, ProviderRoutePayload)
    assert parsed.prompt_version_id == reference  # canonical lower-case UUID text

    with pytest.raises(ValidationError, match="lower-case identifiers"):
        parse_payload("prompt", prompt_payload(variables=["CustomerName"]))
    with pytest.raises(ValidationError, match="must be unique"):
        parse_payload("prompt", prompt_payload(variables=["ticket", "ticket"]))
    with pytest.raises(ValidationError):
        parse_payload("prompt", prompt_payload(output_format="yaml"))


@pytest.mark.parametrize("name", ["api_key", "openai_api_key", "access_token", "secret"])
def test_prompt_variables_may_not_be_named_like_a_secret(name: str) -> None:
    """A placeholder named after a credential is how a key gets interpolated
    into a template at call time, so the shape itself is refused."""
    with pytest.raises(ValidationError, match="looks like a secret name"):
        parse_payload("prompt", prompt_payload(variables=["customer_name", name]))


# ---------------------------------------------------------------------------
# Where sensitive material may live
# ---------------------------------------------------------------------------


def test_provider_route_versions_carry_no_body_and_no_reference() -> None:
    payload = parse_payload("provider_route", route_payload())
    validate_version_pairing("provider_route", payload, body=None, secret_ref=None)
    with pytest.raises(PayloadSemanticsError, match="cannot carry a prompt body"):
        validate_version_pairing("provider_route", payload, body="hello", secret_ref=None)
    with pytest.raises(PayloadSemanticsError, match="cannot carry a secret reference"):
        validate_version_pairing(
            "provider_route", payload, body=None, secret_ref="secret://env/OPENAI_API_KEY"
        )


def test_prompt_version_requires_exactly_one_of_encrypted_body_or_reference() -> None:
    payload = parse_payload("prompt", prompt_payload())
    validate_version_pairing("prompt", payload, body=PROMPT_BODY, secret_ref=None)
    validate_version_pairing("prompt", payload, body=None, secret_ref="secret://vault/triage-v1")
    with pytest.raises(PayloadSemanticsError, match="not both"):
        validate_version_pairing(
            "prompt", payload, body=PROMPT_BODY, secret_ref="secret://vault/triage-v1"
        )
    with pytest.raises(PayloadSemanticsError, match="require a body"):
        validate_version_pairing("prompt", payload, body=None, secret_ref=None)
    # Whitespace is not a body: it would leave an unattributable version.
    with pytest.raises(PayloadSemanticsError, match="require a body"):
        validate_version_pairing("prompt", payload, body="   ", secret_ref=None)


def test_secret_reference_must_be_a_locator_never_a_payload() -> None:
    payload = parse_payload("prompt", prompt_payload())
    for forged in ("env/TRIAGE_KEY", "secret://inline{json}", "secret://with space"):
        with pytest.raises(PayloadSemanticsError, match="secret:// locator"):
            validate_version_pairing("prompt", payload, body=None, secret_ref=forged)


def test_prompt_body_size_is_bounded() -> None:
    payload = parse_payload("prompt", prompt_payload())
    with pytest.raises(PayloadSemanticsError, match="character limit"):
        validate_version_pairing(
            "prompt", payload, body="x" * (MAX_BODY_CHARS + 1), secret_ref=None
        )


def test_slot_names_are_canonicalized_and_constrained() -> None:
    assert validate_slot_name("  support-chat.route_1 ") == "support-chat.route_1"
    for bad in ("", "Upper", "1leading", "has space", "x" * 129, "-dash-first"):
        with pytest.raises(PayloadSemanticsError):
            validate_slot_name(bad)


# ---------------------------------------------------------------------------
# Content addressing
# ---------------------------------------------------------------------------


def test_canonical_json_is_sorted_compact_and_stable() -> None:
    assert canonical_json({"b": 1, "a": "x"}) == '{"a":"x","b":1}'
    assert canonical_json({"a": 1}) != canonical_json({"a": 1.0})  # type stays visible
    assert json.loads(canonical_json(route_payload())) == route_payload()


def test_content_hash_ignores_key_order_but_tracks_body_and_reference() -> None:
    first = content_hash("prompt", {"a": 1, "b": 2}, body="v1")
    second = content_hash("prompt", {"b": 2, "a": 1}, body="v1")
    assert first == second
    assert len(first) == 64
    assert first != content_hash("prompt", {"a": 1, "b": 2}, body="v2")
    assert first != content_hash("prompt", {"a": 1, "b": 2}, secret_ref="secret://x/y")
    assert first != content_hash("provider_route", {"a": 1, "b": 2}, body="v1")


def test_content_hash_segments_cannot_be_re_split_into_a_different_input() -> None:
    """Length-prefixed segments: a body containing the delimiters must not be
    confusable with a reference/body pair."""
    sneaky = "x\x00secret_ref\x01y"
    assert content_hash("prompt", {}, body=sneaky) != content_hash(
        "prompt", {}, body="y", secret_ref="x\x00secret_ref\x01y"
    )


def test_fingerprint_is_short_deterministic_and_non_reversible() -> None:
    token = fingerprint(PROMPT_BODY)
    assert token == fingerprint(PROMPT_BODY)
    assert token.startswith("sha256:") and len(token) == len("sha256:") + 16
    assert PROMPT_BODY not in token


# ---------------------------------------------------------------------------
# Redaction & diff
# ---------------------------------------------------------------------------


def test_every_schema_field_stays_visible_in_a_diff() -> None:
    """Redaction must not blind operators to legitimate metadata: the whole
    point is that *only* secret-shaped names are hidden."""
    for payload in (
        parse_payload("provider_route", route_payload()).model_dump(mode="json"),
        parse_payload("prompt", prompt_payload()).model_dump(mode="json"),
    ):
        assert redact(payload) == payload


@pytest.mark.parametrize(
    "name",
    [
        "api_key",
        "OPENAI_APIKEY",
        "secret",
        "secret_ref",
        "password",
        "access_token",
        "private_key",
        "prompt_template",
        "body",
        "max_output_tokens",  # must NOT be redacted: 'tokens' is not 'token'
        "allowed_providers",
        "evaluator_version",
    ],
)
def test_is_sensitive_name_matches_secret_shaped_names_only(name: str) -> None:
    expected = name not in {"max_output_tokens", "allowed_providers", "evaluator_version"}
    assert is_sensitive_name(name) is expected, name


def test_redact_replaces_sensitive_leaves_with_a_comparable_marker() -> None:
    redacted = redact(
        {
            "provider": "openai",
            "api_key": f"{API_KEY_PREFIX}0123",
            "nested": {"prompt_template": "SECRET TEXT", "timeout_ms": 10},
            "items": [{"secret": "a"}, {"secret": "b"}],
        }
    )
    assert redacted["provider"] == "openai"
    assert redacted["nested"]["timeout_ms"] == 10
    assert redacted["api_key"].startswith(REDACTED_MARKER)
    assert "0123" not in redacted["api_key"]
    assert "SECRET TEXT" not in json.dumps(redacted)
    # Equal values still hash equal, so a diff can answer "did it change?".
    assert redacted["items"][0]["secret"] != redacted["items"][1]["secret"]
    assert redact({"api_key": f"{API_KEY_PREFIX}0123"})["api_key"] == redacted["api_key"]


def test_diff_payloads_recurses_dicts_and_compares_lists_atomically() -> None:
    before = parse_payload(
        "provider_route", route_payload(allowed_providers=["openai"], quality_floor=0.5)
    ).model_dump(mode="json")
    after = parse_payload(
        "provider_route",
        route_payload(
            allowed_providers=["anthropic", "openai"],
            quality_floor=0.6,
            capabilities={"streaming": True, "tools": True, "vision": False},
        ),
    ).model_dump(mode="json")
    entries = diff_payloads(before, after)
    by_path = {entry.path: entry for entry in entries}
    assert by_path["payload.quality_floor"].change == "changed"
    assert (by_path["payload.quality_floor"].before, by_path["payload.quality_floor"].after) == (
        0.5,
        0.6,
    )
    # Reordering a provider preference list IS a routing change: shown whole.
    assert by_path["payload.allowed_providers"].change == "changed"
    assert by_path["payload.allowed_providers"].before == ["openai"]
    # Nested capabilities report the exact leaf that flipped, and only that one.
    assert by_path["payload.capabilities.tools"].change == "changed"
    assert "payload.capabilities.streaming" not in by_path
    assert "payload.capabilities.vision" not in by_path
    assert DiffEntry(path="x", change="added", after=1).before is None


def test_diff_payloads_reports_added_and_removed_fields() -> None:
    entries = diff_payloads({"quality_floor": 0.5}, {"quality_floor": 0.5, "timeout_ms": 10})
    assert [entry.path for entry in entries] == ["payload.timeout_ms"]
    assert entries[0].change == "added" and entries[0].before is None
    assert diff_payloads({"timeout_ms": 10}, {"timeout_ms": 10}) == []


def test_diff_payloads_hides_values_added_by_a_future_schema() -> None:
    """Redaction is by name, so stored JSON written before a field was known
    cannot leak through the diff path."""
    entries = diff_payloads({"api_key": "old-value"}, {"api_key": "new-value"}, prefix="payload")
    assert [entry.path for entry in entries] == ["payload.api_key"]
    assert entries[0].change == "changed"
    assert "old-value" not in str(entries[0]) and "new-value" not in str(entries[0])
    assert entries[0].before.startswith(REDACTED_MARKER)


def test_diff_body_reports_change_with_fingerprints_or_nothing() -> None:
    assert diff_body("abc", "abc") is None
    entry = diff_body("abc", "def")
    assert entry is not None
    assert entry.path == "body" and entry.change == "changed"
    assert entry.before == fingerprint("abc") and entry.after == fingerprint("def")


# ---------------------------------------------------------------------------
# Secret-material guards shared with the audit trail
# ---------------------------------------------------------------------------


def test_day014_audit_vocabulary_exists_and_is_stable() -> None:
    assert AuditAction.CONFIG_VERSION_CREATED == "config_version.created"
    assert AuditAction.CONFIG_VERSION_BODY_REVEALED == "config_version.body_revealed"
    assert AuditAction.CONFIG_ACTIVATION_CREATED == "config_activation.created"
    assert AuditResource.CONFIG_VERSION == "config_version"
    assert AuditResource.CONFIG_ACTIVATION == "config_activation"


def test_audit_detail_refuses_sealed_bodies_as_well_as_raw_keys() -> None:
    """Day 014's widening: once prompt bodies are encrypted at rest, a
    ciphertext blob is exactly as sensitive as an API key."""
    assert FORBIDDEN_DETAIL_MARKERS == (API_KEY_PREFIX, "fernet:", "gAAAAA")
    sealed = FernetVault(MASTER_SECRET).seal(PROMPT_BODY)
    for detail in (
        {"body": f"{API_KEY_PREFIX}aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        {"nested": {"leaked": sealed}},
        {"qualification": {"run": "gAAAAA..."}},
    ):
        with pytest.raises(ValueError, match="secret or sealed-payload material"):
            serialize_detail(detail)
    # A content hash is a hex digest: always safe, so the guard must not fuss.
    assert serialize_detail({"content_hash": content_hash("prompt", {})}) is not None


def test_qualification_guard_rejects_secret_shaped_nested_values() -> None:
    _reject_secret_material(
        {"evaluator": "eval-v2", "scores": {"quality": 0.91}}, field="qualification"
    )
    with pytest.raises(Exception, match="must not contain secret"):
        _reject_secret_material(
            {"evidence": ["ok", FernetVault(MASTER_SECRET).seal("s")]}, field="qualification"
        )
