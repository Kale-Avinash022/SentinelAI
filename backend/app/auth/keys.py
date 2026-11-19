"""API-key generation, digests, scopes and usability rules (Day 013).

Pure domain helpers with no I/O: the endpoints and the store compose them,
the security tests exercise them directly. Rules:

* **CSPRNG only.** The secret is 128 bits of ``secrets.token_hex`` — never a
  UUID-of-convenience, never derived from timestamps or counters.
* **Hash, don't KDF.** Like refresh tokens, the preimage is uniformly random
  and high-entropy, so SHA-256 (via :func:`app.auth.security.token_digest`)
  is the correct irreversible commitment: a leaked table cannot be brute
  forced, and the raw key is never stored or logged.
* **Raw key shown once.** The only place the full value exists server-side is
  the create/rotate response body; everything persisted is prefix + digest.
* **Rotation overlap is bounded.** After rotation the predecessor stays valid
  until ``min(previous expiry, now + grace)`` — a deliberate, reviewable
  window. Revocation ignores all of that and is immediate.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.auth.security import token_digest
from app.models.api_key import KEY_SCOPE_VALUES

# The recognizable machine-credential marker. Not a secret and not a
# password: it is echoed in listings (as part of ``key_prefix``) by design.
API_KEY_PREFIX = "sk_live_"  # nosec B105 - public identifier marker

_SECRET_BYTES = 16  # 128 bits of CSPRNG entropy, hex-encoded (32 chars)
_SECRET_HEX_CHARS = _SECRET_BYTES * 2
#: How much of the secret half is displayable in listings (never the rest).
DISPLAY_SECRET_CHARS = 12


class KeyState(StrEnum):
    """Derived usability of a key at one instant (never stored)."""

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class GeneratedKey:
    """Everything issuance produces. ``raw`` is transient by contract: it is
    returned to the caller once and must never reach a log, audit detail or
    database column."""

    raw: str
    key_prefix: str
    secret_hash: str


def generate_api_key() -> GeneratedKey:
    """Mint one high-entropy key; return raw value, display prefix, digest."""
    secret = secrets.token_hex(_SECRET_BYTES)
    raw = f"{API_KEY_PREFIX}{secret}"
    return GeneratedKey(
        raw=raw,
        key_prefix=f"{API_KEY_PREFIX}{secret[:DISPLAY_SECRET_CHARS]}",
        secret_hash=token_digest(raw),
    )


def is_api_key_shape(value: str) -> bool:
    """True when ``value`` has the exact wire shape of a raw key.

    Structural only (no secret material involved): used to fail clearly on
    malformed credentials before any digest/lookup work happens.
    """
    if not value.startswith(API_KEY_PREFIX):
        return False
    secret = value.removeprefix(API_KEY_PREFIX)
    return len(secret) == _SECRET_HEX_CHARS and all(c in "0123456789abcdef" for c in secret)


def canonicalize_scopes(scopes: Iterable[str]) -> str:
    """Validate, de-duplicate and sort scopes into the canonical stored form.

    Unknown scope names raise ``ValueError`` (mapped to a 422 at the edge):
    the allow-list is the ``KeyScope`` enum, and clients may not invent
    capabilities.
    """
    items = {str(scope) for scope in scopes}
    if not items:
        raise ValueError("at least one scope is required")
    unknown = items - KEY_SCOPE_VALUES
    if unknown:
        raise ValueError(f"unknown API key scope(s): {sorted(unknown)}")
    return ",".join(sorted(items))


def parse_scopes(canonical: str) -> frozenset[str]:
    """Inverse of :func:`canonicalize_scopes` for stored values."""
    return frozenset(part for part in canonical.split(",") if part)


def derive_key_state(
    *,
    revoked_at: datetime | None,
    expires_at: datetime | None,
    now: datetime,
) -> KeyState:
    """One-stop usability truth. Revocation wins over everything; expiry next."""
    if revoked_at is not None:
        return KeyState.REVOKED
    if expires_at is not None and now >= expires_at:
        return KeyState.EXPIRED
    return KeyState.ACTIVE


def rotation_overlap_expiry(
    *,
    previous_expires_at: datetime | None,
    grace_seconds: int,
    now: datetime,
) -> datetime:
    """When the rotated-away predecessor stops working.

    ``min`` of its own scheduled expiry and ``now + grace``: a grace window
    can only *shorten* a key's life, never extend it, and grace 0 collapses
    the overlap to an immediate revocation-equivalent.
    """
    grace_end = now + timedelta(seconds=grace_seconds)
    if previous_expires_at is None:
        return grace_end
    return min(previous_expires_at, grace_end)


__all__ = [
    "API_KEY_PREFIX",
    "DISPLAY_SECRET_CHARS",
    "GeneratedKey",
    "KeyState",
    "canonicalize_scopes",
    "derive_key_state",
    "generate_api_key",
    "is_api_key_shape",
    "parse_scopes",
    "rotation_overlap_expiry",
]
