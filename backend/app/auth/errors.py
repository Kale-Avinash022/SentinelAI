"""Authentication/authorization errors mapped onto the standard envelope.

These subclass :class:`app.core.errors.AppError` so every auth failure renders
the exact same ``{code, message, request_id, retryable}`` body as the rest of
the API — no bespoke error shapes leak from the auth boundary.

``message`` strings are deliberately generic: they must never reveal whether an
account exists, whether the password (vs. the username) was wrong, or which
token check failed, so the layer offers no oracle for enumeration or probing.
"""

from __future__ import annotations

from fastapi import status

from app.core.errors import AppError


class AuthenticationError(AppError):
    """A missing, malformed, expired or otherwise invalid access credential."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"
    retryable = False


class InvalidCredentialsError(AppError):
    """Wrong email *or* password — one indistinguishable generic failure."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "invalid_credentials"
    retryable = False


class PermissionDeniedError(AppError):
    """Authenticated, but the role matrix does not grant the capability."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"
    retryable = False


class RateLimitedError(AppError):
    """Too many login attempts in the window; back off and retry later."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "too_many_requests"
    retryable = True
