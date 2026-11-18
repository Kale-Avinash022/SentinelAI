"""Password hashing and constant-time helpers (Day 012).

Rules enforced here:

* **Argon2id only.** The memory-hard Argon2id variant (RFC 9106) is the single
  accepted algorithm; the parameters are pinned so a hash written by one
  process verifies identically in another and a weak configuration can never be
  negotiated at runtime.
* **Never a bare raise / never a log.** :func:`verify_password` collapses every
  Argon2 failure (mismatch, malformed/corrupt hash, wrong type) to ``False`` so
  an invalid hash can never surface as a stack trace, and no argument — the
  plaintext or the stored digest — is ever written to a log record.
* **Constant-time comparison** is reserved for *derived secrets* we already
  hold as bytes (the refresh-token digest), not for the Argon2 hash itself:
  Argon2's own ``verify`` already runs in constant time internally.

The plaintext password is accepted only as an argument and is never stored,
returned, or represented — keep it out of ``repr``/logs exactly as
``SecretStr`` keeps the signing key out of them.
"""

from __future__ import annotations

import hashlib
import hmac
from functools import lru_cache

from argon2 import PasswordHasher, Type
from argon2.exceptions import Argon2Error, InvalidHashError

# Pinned Argon2id cost parameters (>= OWASP minimums: m=19 MiB, t=2, p=1).
# Raising them later is a transparent rehash-on-login via ``needs_rehash``.
_TIME_COST = 3
_MEMORY_COST = 65536  # KiB (64 MiB)
_PARALLELISM = 4
_HASH_LEN = 32
_SALT_LEN = 16


@lru_cache(maxsize=1)
def _hasher() -> PasswordHasher:
    """Process-wide Argon2 hasher with the pinned Argon2id parameters."""
    return PasswordHasher(
        time_cost=_TIME_COST,
        memory_cost=_MEMORY_COST,
        parallelism=_PARALLELISM,
        hash_len=_HASH_LEN,
        salt_len=_SALT_LEN,
        type=Type.ID,
    )


def hash_password(password: str) -> str:
    """Return an Argon2id PHC string digest for ``password``.

    The returned hash embeds the algorithm, parameters and a fresh random salt,
    so identical passwords produce distinct digests. ``password`` is used only
    in-process and never logged or returned.
    """
    if not password:
        raise ValueError("cannot hash an empty password")
    return _hasher().hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """True when ``password`` matches ``password_hash``.

    Any verification failure — mismatch, ``None``, or a corrupt/foreign digest —
    yields ``False`` rather than raising, so callers can return one generic
    "invalid credentials" error without leaking which half failed.
    """
    if not password_hash:
        return False
    try:
        # NOTE argon2-cffi's signature is verify(hash, password) — the stored
        # digest comes first.
        return _hasher().verify(password_hash, password)
    except (Argon2Error, InvalidHashError):
        # Covers VerifyMismatchError, VerificationError and InvalidHashError (a
        # ValueError subclass, so it must be named explicitly): a mismatch and a
        # corrupt/foreign digest are the same generic "no".
        return False


def needs_rehash(password_hash: str | None) -> bool:
    """True when the stored digest used weaker/older parameters than pinned.

    Login handlers use this to transparently rehash a successfully verified
    password, migrating users to current costs without a forced reset.
    """
    if not password_hash:
        return False
    try:
        return _hasher().check_needs_rehash(password_hash)
    except Argon2Error:
        return True


def constant_time_equals(left: str | bytes, right: str | bytes) -> bool:
    """Length-safe, constant-time comparison for derived secret digests.

    Used to compare the SHA-256 of a presented refresh token against the stored
    digest. Inputs are normalized to bytes; ``hmac.compare_digest`` never
    short-circuits on the first differing byte.
    """

    def _as_bytes(value: str | bytes) -> bytes:
        return value.encode("utf-8") if isinstance(value, str) else value

    return hmac.compare_digest(_as_bytes(left), _as_bytes(right))


def token_digest(token: str) -> str:
    """SHA-256 hex digest of a raw refresh token, for storage/comparison.

    A refresh JWT is already a high-entropy, uniformly random signing output, so
    a plain cryptographic hash (not a slow KDF) is the correct irreversible
    commitment: it hides the token if the session table leaks while staying
    cheap enough to compute on every authenticated refresh.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
