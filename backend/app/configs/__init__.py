"""Versioned configuration domain: payloads, validation, redaction, vault.

Day 014's immutable history lives here as pure rules (no I/O), mirroring the
``app/auth/keys.py`` split from Day 013: the store in
:mod:`app.services.config_versions` composes these, the edge in
:mod:`app.api.v1.configs` only translates HTTP.
"""

from app.configs.rules import (
    CONFIG_KIND_VALUES,
    PRIVACY_CLASS_VALUES,
    ConfigKind,
    DiffEntry,
    ModelCapabilities,
    PrivacyClass,
    PromptPayload,
    ProviderRoutePayload,
    VersionPayload,
    canonical_json,
    content_hash,
    diff_payloads,
    fingerprint,
    parse_payload,
    redact,
    validate_slot_name,
    validate_version_pairing,
)
from app.configs.vault import (
    SEALED_PAYLOAD_MARKER,
    FernetVault,
    PayloadVault,
    VaultError,
    is_sealed,
)

__all__ = [
    "CONFIG_KIND_VALUES",
    "PRIVACY_CLASS_VALUES",
    "SEALED_PAYLOAD_MARKER",
    "ConfigKind",
    "DiffEntry",
    "FernetVault",
    "ModelCapabilities",
    "PayloadVault",
    "PrivacyClass",
    "PromptPayload",
    "ProviderRoutePayload",
    "VaultError",
    "VersionPayload",
    "canonical_json",
    "content_hash",
    "diff_payloads",
    "fingerprint",
    "is_sealed",
    "parse_payload",
    "redact",
    "validate_slot_name",
    "validate_version_pairing",
]
