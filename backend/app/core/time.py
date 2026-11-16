"""Tiny UTC timestamp and id helpers shared by all ORM model defaults."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime


def utcnow() -> datetime:
    """Timezone-aware UTC 'now' for ORM-side column defaults.

    Application-side defaults (not ``server_default``) keep timestamps
    deterministic under tests, avoid per-connection DB clock drift, and give
    Day 022+ telemetry a single, obvious time source to reuse.
    """
    return datetime.now(UTC)


def new_id() -> uuid.UUID:
    """Primary-key default: UUIDv7 (time-ordered, index-friendly) when the
    interpreter provides it (3.14+), falling back to plain UUIDv4 otherwise.
    Both are 128-bit ``uuid.UUID`` values; nothing may depend on ordering."""
    uuid7: Callable[[], uuid.UUID] | None = getattr(uuid, "uuid7", None)
    if uuid7 is not None:
        return uuid7()
    return uuid.uuid4()
