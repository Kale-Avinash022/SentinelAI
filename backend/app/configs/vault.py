"""Encrypted payload adapter for sensitive configuration bodies (Day 014).

Prompt templates are **sensitive configuration, not telemetry**: the step rule
is that bodies may only be at rest inside encrypted storage or behind an
external secret reference. This module owns the encryption half of that rule
and hides it behind a two-method port so the store never handles keys and the
edge never sees ciphertext construction.

Design rules:

* **Authenticated encryption, never a home-grown scheme.** :class:`FernetVault`
  uses Fernet (AES-128-CBC + HMAC-SHA256, verified before decryption), so a
  tampered or foreign-keyed ciphertext fails closed instead of returning
  garbage plaintext.
* **Derived, never raw, master key.** The Fernet key is HKDF-SHA256 expanded
  from ``Settings.secret_key`` with a fixed context string. ``secret_key`` is
  already a ``SecretStr`` that operators rotate as a *single* master secret,
  so config payloads inherit its rotation without a second credential to leak.
  The version suffix reserves room for envelope-style rotation: sealed text
  names the key version that produced it, so an old body stays readable while a
  new one is written under the next key.
* **Ciphertext is self-describing but not self-authorizing.** The ``fernet:v1:``
  prefix is a format marker only; it carries no key material, which is exactly
  why :data:`SEALED_PAYLOAD_MARKER` is banned from the audit trail.
"""

from __future__ import annotations

import base64
import re
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: Prefix written into the ciphertext column. Not a secret, but a *shape*
#: marker: :mod:`app.services.audit` refuses to log anything containing it, so
#: a sealed body can never be smuggled into the trail by mistake.
SEALED_PAYLOAD_MARKER = "fernet:"  # nosec B105 - format prefix, not a credential

_HKDF_SALT = b"sentinelai-config-payload-vault-v1"
_KEY_LENGTH = 32

_SEALED_RE = re.compile(r"^fernet:v(?P<version>\d+):(?P<token>[A-Za-z0-9_\-+=]+)$")


class VaultError(RuntimeError):
    """A sealed payload could not be opened (wrong key, tampering, unknown format).

    Deliberately opaque: the underlying ``InvalidToken`` text never escapes, in
    step with the rest of the codebase where rejection reasons are not a client
    concern.
    """


class PayloadVault(Protocol):
    """The storage-encryption seam the config store depends on."""

    def seal(self, plaintext: str) -> str:
        """Encrypt a sensitive body for at-rest storage."""
        ...

    def open(self, sealed: str) -> str:
        """Decrypt stored ciphertext back to the sensitive body."""
        ...


def is_sealed(value: str) -> bool:
    """True when ``value`` has the shape of this module's sealed output."""
    return bool(_SEALED_RE.match(value))


class FernetVault:
    """:class:`PayloadVault` backed by Fernet with an HKDF-derived key.

    Deterministic in key derivation, randomized in output: Fernet embeds a fresh
    IV and timestamp per call, so sealing the same body twice yields different
    ciphertexts. Version equality is therefore proven with
    :func:`~app.configs.rules.content_hash` over the plaintext, never over the
    ciphertext column.
    """

    def __init__(self, master_secret: str, *, key_version: int = 1) -> None:
        if key_version < 1:
            raise ValueError("key_version must be positive")
        if not master_secret:
            raise ValueError("payload vault requires a non-empty master secret")
        self._key_version = key_version
        self._fernet = Fernet(self._derive_key(master_secret, key_version))

    @staticmethod
    def _derive_key(master_secret: str, key_version: int) -> bytes:
        """HKDF-SHA256 expansion of the master secret into a Fernet key."""
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=_KEY_LENGTH,
            salt=_HKDF_SALT,
            info=f"sentinelai-config-payload-key-v{key_version}".encode(),
        ).derive(master_secret.encode())
        return base64.urlsafe_b64encode(derived)

    def seal(self, plaintext: str) -> str:
        token = self._fernet.encrypt(plaintext.encode()).decode("ascii")
        return f"{SEALED_PAYLOAD_MARKER}v{self._key_version}:{token}"

    def open(self, sealed: str) -> str:
        match = _SEALED_RE.match(sealed)
        if match is None:
            raise VaultError("Stored payload is not in a recognized sealed format.")
        if int(match.group("version")) != self._key_version:
            # Rotation arrives with a real key ring; until then a foreign key
            # version is an operational error, not a silent re-derive.
            raise VaultError("Stored payload was sealed under a different key version.")
        try:
            plaintext = self._fernet.decrypt(match.group("token").encode("ascii"))
        except (InvalidToken, ValueError) as exc:
            raise VaultError("Stored payload could not be decrypted.") from exc
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:  # authenticated, so this is corruption
            raise VaultError("Stored payload decrypted to invalid text.") from exc


__all__ = ["SEALED_PAYLOAD_MARKER", "FernetVault", "PayloadVault", "VaultError", "is_sealed"]
