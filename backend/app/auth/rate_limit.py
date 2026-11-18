"""In-process fixed-window login throttle (Day 012).

A deliberately minimal, dependency-free guard against brute-force and account
enumeration: each ``(client_ip, email)`` key may attempt the login at most
``max_attempts`` times per rolling ``window_seconds``. It is single-process and
non-persistent by design for the modular-monolith MVP — the trade-off is that a
restart or an extra worker widens the budget — and the runbook flags swapping
in the Redis-backed limiter (available once telemetry/queue land in Week 4) for
multi-instance deployments.

The clock is injectable so tests advance time deterministically without sleeps.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int


class LoginRateLimiter:
    """Token-free sliding-window counter keyed by client identity."""

    def __init__(
        self,
        *,
        max_attempts: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be >= 1")
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: tuple[str, str]) -> RateLimitDecision:
        """Return whether a login attempt for ``key`` is allowed right now.

        Records the attempt when allowed. Does *not* record when denied, so a
        blocked client is not pushed further into the penalty box by probing.
        """
        now = self._clock()
        cutoff = now - self._window_seconds
        with self._lock:
            attempts = self._hits.setdefault(key, deque())
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= self._max_attempts:
                oldest = attempts[0]
                retry_after = int(oldest + self._window_seconds - now) + 1
                return RateLimitDecision(allowed=False, retry_after_seconds=max(retry_after, 1))
            attempts.append(now)
            return RateLimitDecision(allowed=True, retry_after_seconds=0)

    def reset(self, key: tuple[str, str]) -> None:
        """Clear the window for a key after a successful authentication."""
        with self._lock:
            self._hits.pop(key, None)
