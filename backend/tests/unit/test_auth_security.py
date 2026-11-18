"""Unit tests for the Argon2id password helpers and constant-time primitives.

These are pure-CPU tests: no database, no HTTP, no clock dependency. They lock
the Day 012 security contract for :mod:`app.auth.security` — Argon2id only, a
generic ``False`` on any verification failure (never an exception that could
become an oracle), transparent rehash detection, and constant-time digest
comparison.
"""

from __future__ import annotations

from app.auth.security import (
    constant_time_equals,
    hash_password,
    needs_rehash,
    token_digest,
    verify_password,
)

PASSWORD = "correct-horse-battery-staple"


def test_hash_is_argon2id_and_self_describing() -> None:
    digest = hash_password(PASSWORD)
    # PHC prefix "$argon2id$" proves the mandated variant, not argon2i/argon2d.
    assert digest.startswith("$argon2id$")
    # Embedded parameters and a salt mean two hashes of the same secret differ.
    assert digest != hash_password(PASSWORD)


def test_verify_roundtrip_and_mismatch() -> None:
    digest = hash_password(PASSWORD)
    assert verify_password(PASSWORD, digest) is True
    assert verify_password(PASSWORD + "!", digest) is False


def test_verify_never_raises_on_garbage_or_missing() -> None:
    # A corrupt/foreign digest, empty hash and None all collapse to a generic
    # False so the caller can return one indistinguishable credentials error.
    assert verify_password(PASSWORD, "not-a-valid-hash") is False
    assert verify_password(PASSWORD, "") is False
    assert verify_password(PASSWORD, None) is False


def test_needs_rehash_for_weaker_parameters() -> None:
    # A hash produced with lighter costs than the pinned parameters must flag
    # for transparent upgrade on the next successful login.
    from argon2 import PasswordHasher, Type

    weak = PasswordHasher(time_cost=1, memory_cost=16384, parallelism=1, type=Type.ID).hash(
        PASSWORD
    )
    assert needs_rehash(weak) is True
    assert needs_rehash(hash_password(PASSWORD)) is False
    assert needs_rehash(None) is False


def test_token_digest_is_stable_and_64_hex() -> None:
    first = token_digest("some-refresh-token")
    assert first == token_digest("some-refresh-token")
    assert len(first) == 64
    assert first != token_digest("another-refresh-token")
    # A raw token must never appear in its own stored digest.
    assert "some-refresh-token" not in first


def test_constant_time_equals_semantics() -> None:
    assert constant_time_equals("abc", "abc") is True
    assert constant_time_equals(b"abc", "abc") is True
    assert constant_time_equals("abc", "abd") is False
    assert constant_time_equals("abc", "abcd") is False
