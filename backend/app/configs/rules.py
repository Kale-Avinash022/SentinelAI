"""Immutable-version payload rules: kinds, validation, hashing, redacted diffs.

Everything in this module is pure and hermetic so the four Day 014 acceptance
checks can be pinned by unit tests before a single row exists:

* **Closed, typed payloads.** Every version stores non-secret *metadata* under
  a per-kind schema with ``extra="forbid"`` — an unrecognised field is a
  422, never a silently-ignored capability. Model capabilities, allowed
  providers, privacy class, quality floor, timeout/budget limits, evaluator
  version and the prompt reference are exactly the fields the step names, and
  nothing else may be added without a reviewed schema change.
* **Sensitive material never enters the metadata column.** A prompt body must
  arrive as an encrypted payload or an external ``secret://`` reference —
  never as a ``payload`` field — and :func:`validate_version_pairing` is the
  single rule both the edge and the store consult.
* **Content-addressed versions.** :func:`content_hash` is a SHA-256 over the
  canonical metadata JSON, the secret reference and the plaintext body, so two
  versions are provably identical without comparing ciphertext (which is
  randomized per seal) and a diff can show a *fingerprint* change instead of
  the secret value itself.
* **Redaction is the default, not a call-site option.** :func:`redact` walks
  any structure and replaces every sensitive-named value with a marker plus its
  fingerprint, so a diff still answers "did it change, and how similar is it?"
  without ever answering "what was it?".
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.security import token_digest

#: Versioned configuration kinds. Mirrors the ``ck_config_versions_valid_kind``
#: CHECK — adding a kind means migrating, not just editing an enum.
PROVIDER_ROUTE = "provider_route"
PROMPT = "prompt"


class ConfigKind(StrEnum):
    PROVIDER_ROUTE = PROVIDER_ROUTE
    PROMPT = PROMPT


CONFIG_KIND_VALUES: frozenset[str] = frozenset(kind.value for kind in ConfigKind)


class PrivacyClass(StrEnum):
    """How much the routed traffic is allowed to leave the tenant with."""

    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


PRIVACY_CLASS_VALUES: frozenset[str] = frozenset(value.value for value in PrivacyClass)

#: Name of the slot a version ladder belongs to, shared by both tables' CHECK
#: (lower-case, digit-safe, dotted namespaces allowed for future grouping).
SLOT_NAME_PATTERN = r"^[a-z][a-z0-9_.-]{0,127}$"
#: Prompt body ceiling. Bodies are configuration, not documents: a bound here
#: keeps the sealed ciphertext (and the reveal response) bounded.
MAX_BODY_CHARS = 50_000

REDACTED_MARKER = "[REDACTED]"

# Sensitive *value names* that must never render in a diff, response or audit
# detail. Matched as whole underscore/dot-separated segments so legitimate
# fields such as ``max_output_tokens`` or ``allowed_providers`` stay visible
# while ``access_token``, ``api_key``, ``secret_ref`` and ``body`` do not.
_SENSITIVE_NAME_RE = re.compile(
    r"(?:^|[_\-.])"
    r"(?:api[_-]?key|apikey|secret|secret_ref|secrets?|password|passwd|credential"
    r"|credentials|token|access[_-]token|refresh[_-]token|authorization|private[_-]key"
    r"|body|template|prompt_text)"
    r"(?:$|[_\-.])",
    re.IGNORECASE,
)


class PayloadSemanticsError(ValueError):
    """A payload is structurally valid but semantically rejected.

    Raised for rules that span fields or span kinds (a route carrying a body, a
    prompt with neither encrypted storage nor a secret reference). The edge maps
    it to 422, the store re-checks it so a direct caller cannot skip the edge.
    """


# ---------------------------------------------------------------------------
# Typed metadata schemas
# ---------------------------------------------------------------------------


class ModelCapabilities(BaseModel):
    """What the routed model must be able to do for this application."""

    model_config = ConfigDict(extra="forbid")

    streaming: bool = False
    tools: bool = False
    vision: bool = False


class ProviderRoutePayload(BaseModel):
    """Non-secret routing posture for one provider/model pair (Day 008 split).

    Deliberately *no* credential field of any kind: provider secrets stay in
    ``provider_credentials`` as ``secret://`` references, so a version row is
    always safe to read, diff and retain.
    """

    model_config = ConfigDict(extra="forbid")

    primary_provider: str = Field(min_length=1, max_length=64)
    allowed_providers: list[str] = Field(min_length=1, max_length=10)
    model_name: str = Field(min_length=1, max_length=200)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    privacy_class: PrivacyClass = PrivacyClass.INTERNAL
    quality_floor: float = Field(ge=0.0, le=1.0)
    timeout_ms: int = Field(ge=100, le=600_000)
    max_input_tokens: int = Field(ge=1, le=10_000_000)
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    evaluator_version: str = Field(min_length=1, max_length=64)
    prompt_version_id: str | None = None

    @field_validator("allowed_providers")
    @classmethod
    def _providers_are_unique(cls, value: list[str]) -> list[str]:
        cleaned = [provider.strip() for provider in value]
        if any(not provider for provider in cleaned):
            raise ValueError("allowed_providers must not contain blank entries")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("allowed_providers must be unique")
        return cleaned

    @field_validator("prompt_version_id")
    @classmethod
    def _prompt_reference_is_uuid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return str(uuid.UUID(value))
        except ValueError as exc:
            raise ValueError("prompt_version_id must be a UUID") from exc

    def _pairing_problems(self) -> list[str]:
        if self.primary_provider not in self.allowed_providers:
            return ["primary_provider must be one of allowed_providers"]
        return []


class PromptPayload(BaseModel):
    """Metadata describing a prompt template; the template itself is sealed."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=200)
    variables: list[str] = Field(default_factory=list, max_length=32)
    output_format: Literal["text", "json"] = "text"
    max_output_tokens: int = Field(ge=1, le=1_000_000)
    quality_floor: float = Field(default=0.0, ge=0.0, le=1.0)
    evaluator_version: str = Field(min_length=1, max_length=64)

    @field_validator("variables")
    @classmethod
    def _variables_are_valid_placeholders(cls, value: list[str]) -> list[str]:
        cleaned = [name.strip() for name in value]
        for name in cleaned:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
                raise ValueError(
                    "variables must be lower-case identifiers (a-z, digits, underscore)"
                )
            # A placeholder named api_key/token/secret is how a credential ends
            # up interpolated into a template at call time. Prompt variables
            # carry tenant data, not secrets, so refuse the shape outright.
            if is_sensitive_name(name):
                raise ValueError(
                    f"variable {name!r} looks like a secret name; prompt variables "
                    "must never receive credential material"
                )
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("variables must be unique")
        return cleaned

    def _pairing_problems(self) -> list[str]:
        return []


#: payload schema per kind; the store and the edge share this frozen table so
#: an unknown kind can never reach an untyped ``dict`` accept-all path.
PAYLOAD_SCHEMAS: Mapping[str, type[BaseModel]] = MappingProxyType(
    {PROVIDER_ROUTE: ProviderRoutePayload, PROMPT: PromptPayload}
)

VersionPayload = ProviderRoutePayload | PromptPayload


# ---------------------------------------------------------------------------
# Validation entry points
# ---------------------------------------------------------------------------


def validate_slot_name(name: str) -> str:
    """Canonicalize and bound a slot name; anything else is a client bug."""
    cleaned = name.strip()
    if not re.fullmatch(SLOT_NAME_PATTERN, cleaned):
        raise PayloadSemanticsError(
            "name must be lower-case, start with a letter, and use only "
            "a-z 0-9 . _ - (max 128 characters)"
        )
    return cleaned


def parse_payload(kind: ConfigKind | str, payload: Mapping[str, Any]) -> VersionPayload:
    """Validate ``payload`` against the closed schema for ``kind``.

    ``ValidationError`` propagates untouched: the edge's handler turns it into
    the sanitized 422 envelope (field pointers only, never the submitted
    values), and the store catches it to raise the same class of error.
    """
    typed_kind = ConfigKind(kind)
    schema = PAYLOAD_SCHEMAS[typed_kind.value]
    validated = schema.model_validate(dict(payload))
    # ``PAYLOAD_SCHEMAS`` is the single source of truth for kind -> schema; the
    # narrowings below only restate it so the return type stays precise. A new
    # payload class that is not listed here falls through to a rejection rather
    # than being handed back as an untyped ``BaseModel``.
    if isinstance(validated, ProviderRoutePayload):
        return validated
    if isinstance(validated, PromptPayload):
        return validated
    raise PayloadSemanticsError(f"Schema {schema.__name__} is not a version payload")


def validate_version_pairing(
    kind: ConfigKind | str,
    payload: VersionPayload,
    *,
    body: str | None,
    secret_ref: str | None,
) -> None:
    """One rule, both call sites: where the sensitive material may live.

    * a provider route has no body to hide, so it must not carry one;
    * a prompt version must resolve its template through **exactly one** of
      encrypted storage (``body``, sealed by the caller) or an external
      ``secret://`` reference — never neither (untraceable content) and never
      both (which one wins would be a silent ambiguity);
    * a reference must be a locator, matching the same shape the Day 008
      credential CHECK demands, so no payload can ride in as a "reference".
    """
    typed_kind = ConfigKind(kind)
    problems: list[str] = []
    if typed_kind is ConfigKind.PROVIDER_ROUTE:
        if body is not None:
            problems.append("provider_route versions cannot carry a prompt body")
        if secret_ref is not None:
            problems.append("provider_route versions cannot carry a secret reference")
        problems.extend(payload._pairing_problems())
    else:
        if not isinstance(payload, PromptPayload):  # defensive: kind/schema drift
            problems.append("prompt versions require the prompt payload schema")
        has_body = body is not None and body.strip() != ""
        if has_body and secret_ref is not None:
            problems.append("supply either an encrypted body or a secret reference, not both")
        if not has_body and secret_ref is None:
            problems.append(
                "prompt versions require a body (stored encrypted) or a secret:// reference"
            )
        if body is not None and len(body) > MAX_BODY_CHARS:
            problems.append(f"prompt body exceeds the {MAX_BODY_CHARS} character limit")
    if secret_ref is not None:
        ref = secret_ref.strip()
        if not ref.startswith("secret://") or "{" in ref or "}" in ref or " " in ref:
            problems.append("secret_ref must be a secret:// locator, never an inline payload")
    if problems:
        raise PayloadSemanticsError("; ".join(problems))


# ---------------------------------------------------------------------------
# Canonicalization & content addressing
# ---------------------------------------------------------------------------


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Stable JSON text: sorted keys, no whitespace, non-JSON types stringified.

    The stored ``payload`` column and :func:`content_hash` both use this form so
    a version's identity does not depend on the insertion order of the client's
    JSON object.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: str) -> str:
    """Short, comparable commitment used wherever the raw value must not render."""
    return f"sha256:{token_digest(value)[:16]}"


def content_hash(
    kind: ConfigKind | str,
    payload: Mapping[str, Any],
    *,
    body: str | None = None,
    secret_ref: str | None = None,
) -> str:
    """SHA-256 of a version's *logical* content (64 hex chars, no ciphertext).

    Length-prefixed segments so no pair of inputs can collide by concatenation
    ambiguity (``{"a": "bc"}`` vs ``{"ab": "c"}``), and the plaintext body is
    folded in even though it is never stored in the clear — that is what lets a
    redacted diff prove a template changed.
    """
    typed_kind = ConfigKind(kind)
    segments: tuple[tuple[str, str], ...] = (
        ("kind", typed_kind.value),
        ("payload", canonical_json(payload)),
        ("secret_ref", secret_ref or ""),
        ("body", body or ""),
    )
    # ``<length>\0<label>\1<value>`` per segment: a body may contain any text,
    # including the delimiters, and still cannot be re-split into a different
    # (secret_ref, body) pair that hashes identically.
    material = "".join(f"{len(value)}\x00{label}\x01{value}" for label, value in segments)
    return token_digest(material)


# ---------------------------------------------------------------------------
# Redaction & diff
# ---------------------------------------------------------------------------


def is_sensitive_name(name: str) -> bool:
    return bool(_SENSITIVE_NAME_RE.search(name))


def redact(value: Any, *, path: str = "") -> Any:
    """Deep-copy ``value`` replacing every sensitive leaf with a fingerprint.

    Unknown-shape values are handled too: this runs on stored JSON, which may
    have been written by an older (or future) schema, so the safe default is
    "redact by name", not "trust the schema".
    """
    if path and is_sensitive_name(path.rsplit(".", 1)[-1]):
        if isinstance(value, str):
            return f"{REDACTED_MARKER} {fingerprint(value)}"
        if isinstance(value, (Mapping, Sequence)) and not isinstance(value, str):
            return f"{REDACTED_MARKER} {fingerprint(canonical_json(_stringify(value)))}"
        return REDACTED_MARKER
    if isinstance(value, Mapping):
        return {
            str(key): redact(item, path=f"{path}.{key}" if path else str(key))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, path=path) for item in value]
    return value


def _stringify(value: Any) -> Any:
    """Make a nested structure JSON-renderable without losing redaction input."""
    if isinstance(value, Mapping):
        return {str(key): _stringify(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stringify(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class DiffEntry:
    """One field-level change between two versions, already redacted."""

    path: str
    change: Literal["added", "removed", "changed"]
    before: Any = None
    after: Any = None


def diff_payloads(
    before: Mapping[str, Any], after: Mapping[str, Any], *, prefix: str = "payload"
) -> list[DiffEntry]:
    """Field-level diff of two metadata mappings, values redacted by name.

    Dicts recurse to leaf granularity (so an operator sees exactly *which*
    capability flipped), while lists compare atomically — reordering a provider
    preference list *is* a routing change, and pretending otherwise would hide
    the most consequential kind of edit.
    """
    entries: list[DiffEntry] = []
    keys = set(before) | set(after)
    for key in sorted(keys):
        path = f"{prefix}.{key}"
        in_before, in_after = key in before, key in after
        old_value, new_value = before.get(key), after.get(key)
        if in_before and in_after:
            if isinstance(old_value, Mapping) and isinstance(new_value, Mapping):
                entries.extend(diff_payloads(old_value, new_value, prefix=path))
                continue
            if old_value != new_value:
                entries.append(
                    DiffEntry(
                        path=path,
                        change="changed",
                        before=redact(old_value, path=path),
                        after=redact(new_value, path=path),
                    )
                )
        elif in_after:
            entries.append(DiffEntry(path=path, change="added", after=redact(new_value, path=path)))
        else:
            entries.append(
                DiffEntry(path=path, change="removed", before=redact(old_value, path=path))
            )
    return entries


def diff_body(before_hash: str, after_hash: str) -> DiffEntry | None:
    """Represent the sealed template change as fingerprints only.

    The body itself is never available to this function by construction: the
    store passes content hashes, so a diff of prompt text cannot leak plaintext
    even if a future caller forgets to redact.
    """
    if before_hash == after_hash:
        return None
    return DiffEntry(
        path="body",
        change="changed",
        before=fingerprint(before_hash),
        after=fingerprint(after_hash),
    )


__all__ = [
    "CONFIG_KIND_VALUES",
    "MAX_BODY_CHARS",
    "PAYLOAD_SCHEMAS",
    "PRIVACY_CLASS_VALUES",
    "PROVIDER_ROUTE",
    "PROMPT",
    "REDACTED_MARKER",
    "SLOT_NAME_PATTERN",
    "ConfigKind",
    "DiffEntry",
    "ModelCapabilities",
    "PayloadSemanticsError",
    "PrivacyClass",
    "PromptPayload",
    "ProviderRoutePayload",
    "VersionPayload",
    "canonical_json",
    "content_hash",
    "diff_body",
    "diff_payloads",
    "fingerprint",
    "is_sensitive_name",
    "parse_payload",
    "redact",
    "validate_slot_name",
    "validate_version_pairing",
]
