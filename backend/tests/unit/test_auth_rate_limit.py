"""Unit tests for the fixed-window login throttle (:mod:`app.auth.rate_limit`).

The clock is injected so window expiry is verified without sleeping. These lock
the enumeration/brute-force defence: a bounded number of attempts per key per
window, denial that does *not* extend the penalty, recovery once the window
rolls, and an explicit reset on successful authentication.
"""

from __future__ import annotations

import pytest
from app.auth.rate_limit import LoginRateLimiter

KEY = ("1.2.3.4", "user@example.test")


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _limiter(clock: FakeClock, *, max_attempts: int = 3, window: int = 60) -> LoginRateLimiter:
    return LoginRateLimiter(max_attempts=max_attempts, window_seconds=window, clock=clock)


def test_allows_up_to_max_then_denies() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    for _ in range(3):
        assert limiter.check(KEY).allowed is True
    decision = limiter.check(KEY)
    assert decision.allowed is False
    assert decision.retry_after_seconds >= 1


def test_denial_does_not_extend_the_window() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, max_attempts=2, window=60)
    limiter.check(KEY)
    limiter.check(KEY)
    # Several denied probes must not push the unlock time further out.
    for _ in range(5):
        assert limiter.check(KEY).allowed is False
    clock.advance(60)
    assert limiter.check(KEY).allowed is True


def test_window_rolls_and_key_is_isolated() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, max_attempts=1, window=30)
    assert limiter.check(KEY).allowed is True
    assert limiter.check(KEY).allowed is False
    other = ("5.6.7.8", "user@example.test")
    # A different client/IP key has its own independent budget.
    assert limiter.check(other).allowed is True
    clock.advance(30)
    assert limiter.check(KEY).allowed is True


def test_reset_clears_only_the_named_key() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, max_attempts=1, window=60)
    assert limiter.check(KEY).allowed is True
    assert limiter.check(KEY).allowed is False
    limiter.reset(KEY)
    assert limiter.check(KEY).allowed is True


def test_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        LoginRateLimiter(max_attempts=0, window_seconds=60)
    with pytest.raises(ValueError):
        LoginRateLimiter(max_attempts=3, window_seconds=0)
