"""Normalized LLM gateway contracts (Day 015).

The internal contract every chat caller, fake adapter and (from Day 016)
LiteLLM adapter must speak. Business logic is coupled to *these* closed types,
never to a vendor's response dictionary: a provider's own shape is translated
once, at the adapter boundary, and everything downstream sees exactly
:class:`ProviderCompletion`, :class:`ProviderUsage` and the typed
:class:`ProviderFailure` family.

Three normalization decisions the later steps depend on:

* **Usage carries a truth label.** :class:`UsageKind` is ``exact`` only when
  the provider counted tokens for this call, ``estimated`` when the gateway
  derived the number locally, and ``unknown`` when nobody could. Reports and
  budgets must never silently treat an estimate as a bill.
* **Provider failures are a closed enum plus typed exceptions.** The seven
  brief codes (``INVALID_REQUEST`` … ``INTERNAL_ERROR``) map to a fixed HTTP
  status, an envelope ``code`` and a ``retryable`` flag, so every adapter
  surfaces the same stable envelope and a client can program against
  retryability without parsing messages.
* **Every result is traceable.** ``trace_id`` rides the correlation id from
  :class:`~app.core.middleware.CorrelationIdMiddleware`, so a normalized
  result can always be tied back to one HTTP request.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from fastapi import status

from app.core.errors import AppError

# ---------------------------------------------------------------------------
# Identity constants
# ---------------------------------------------------------------------------

#: The brief's virtual model: the gateway, not the caller, picks the provider.
AUTO_MODEL = "sentinel-auto"

#: Provider identity of the deterministic fake family (never a real vendor).
FAKE_PROVIDER = "fake"
#: Model identity the fakes report — stable so tests can pin it.
FAKE_MODEL = "sentinel-fake-v1"

#: The only roles allowed to invoke the chat gateway (ADR-0004: Viewer is
#: read-only; proposing/answering with a model is an operator action).
CHAT_ALLOWED_ROLES = frozenset({"owner", "admin", "operator"})

ChatRole = Literal["owner", "admin", "operator"]


class UsageKind(enum.StrEnum):
    """How much the token counts in a :class:`ProviderUsage` can be trusted."""

    EXACT = "exact"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class GatewayErrorCode(enum.StrEnum):
    """The brief's seven standard provider error codes."""

    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class _ErrorSpec:
    """Fixed HTTP translation of one standard code — no per-call improvising."""

    http_status: int
    envelope_code: str
    retryable: bool


#: The single mapping table every adapter and the service share.
GATEWAY_ERROR_SPECS: dict[GatewayErrorCode, _ErrorSpec] = {
    GatewayErrorCode.INVALID_REQUEST: _ErrorSpec(
        status.HTTP_400_BAD_REQUEST, "invalid_request", False
    ),
    GatewayErrorCode.UNAUTHORIZED: _ErrorSpec(
        status.HTTP_401_UNAUTHORIZED, "unauthenticated", False
    ),
    GatewayErrorCode.FORBIDDEN: _ErrorSpec(status.HTTP_403_FORBIDDEN, "permission_denied", False),
    GatewayErrorCode.RATE_LIMITED: _ErrorSpec(
        status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited", True
    ),
    GatewayErrorCode.PROVIDER_TIMEOUT: _ErrorSpec(
        status.HTTP_504_GATEWAY_TIMEOUT, "provider_timeout", True
    ),
    GatewayErrorCode.PROVIDER_UNAVAILABLE: _ErrorSpec(
        status.HTTP_503_SERVICE_UNAVAILABLE, "provider_unavailable", True
    ),
    GatewayErrorCode.INTERNAL_ERROR: _ErrorSpec(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error", False
    ),
}

# ---------------------------------------------------------------------------
# Normalized domain types (internal — never vendor dictionaries)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Token accounting for one call, always with its truth label.

    ``total_tokens`` is derived, never trusted from a vendor: it is exactly
    ``input + output`` whenever both are known, and ``None`` otherwise.
    """

    kind: UsageKind
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """The gateway-normalized view of a chat call, passed to any adapter."""

    provider: str
    model: str
    messages: tuple[ChatMessageView, ...]
    max_output_tokens: int
    timeout_ms: int
    trace_id: str | None = None
    options: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChatMessageView:
    """One message inside a :class:`CompletionRequest` (role + content only)."""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ProviderCompletion:
    """A normalized success: content plus identity, usage, latency and trace.

    ``latency_ms`` is measured by the gateway around the adapter call, so it
    compares like-for-like across providers. Prompt/response text lives only
    in this immediate call path — persistence layers (Day 017+) store
    metadata derived from it, never the content itself.
    """

    content: str
    provider: str
    model: str
    usage: ProviderUsage
    latency_ms: int
    trace_id: str | None = None
    finish_reason: str = "stop"


# ---------------------------------------------------------------------------
# Typed provider failure family
# ---------------------------------------------------------------------------


class ProviderFailure(AppError):
    """A provider call that did not succeed, in exactly one standard shape.

    Adapters translate vendor-specific exceptions into one of the subclasses
    below; the service layer never catches anything else from an adapter, so
    a vendor error can never escape as a bare 500 with internals attached.
    ``message`` is the generic, operator-safe text — never the vendor payload.
    """

    gateway_code: GatewayErrorCode

    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds

    def __str__(self) -> str:
        """Include the standard code so logs and reprs name the contract value."""
        return f"{self.gateway_code.value}: {self.message}"


class InvalidRequestProviderError(ProviderFailure):
    """The provider rejected the request itself."""

    gateway_code = GatewayErrorCode.INVALID_REQUEST
    status_code = GATEWAY_ERROR_SPECS[GatewayErrorCode.INVALID_REQUEST].http_status
    code = GATEWAY_ERROR_SPECS[GatewayErrorCode.INVALID_REQUEST].envelope_code


class UnauthorizedProviderError(ProviderFailure):
    """The provider rejected our credentials for it."""

    gateway_code = GatewayErrorCode.UNAUTHORIZED
    status_code = GATEWAY_ERROR_SPECS[GatewayErrorCode.UNAUTHORIZED].http_status
    code = GATEWAY_ERROR_SPECS[GatewayErrorCode.UNAUTHORIZED].envelope_code


class ForbiddenProviderError(ProviderFailure):
    """Credentials valid, but not allowed for this model/quota."""

    gateway_code = GatewayErrorCode.FORBIDDEN
    status_code = GATEWAY_ERROR_SPECS[GatewayErrorCode.FORBIDDEN].http_status
    code = GATEWAY_ERROR_SPECS[GatewayErrorCode.FORBIDDEN].envelope_code


class RateLimitedProviderError(ProviderFailure):
    """Provider-side throttling; the caller should back off and retry."""

    gateway_code = GatewayErrorCode.RATE_LIMITED
    status_code = GATEWAY_ERROR_SPECS[GatewayErrorCode.RATE_LIMITED].http_status
    code = GATEWAY_ERROR_SPECS[GatewayErrorCode.RATE_LIMITED].envelope_code
    retryable = True


class ProviderTimeoutError(ProviderFailure):
    """The provider did not answer within the gateway's deadline.

    Ambiguous by nature (Day 017): the call may still have been billed, so
    automated replay is prohibited until the attempt ledger settles it.
    """

    gateway_code = GatewayErrorCode.PROVIDER_TIMEOUT
    status_code = GATEWAY_ERROR_SPECS[GatewayErrorCode.PROVIDER_TIMEOUT].http_status
    code = GATEWAY_ERROR_SPECS[GatewayErrorCode.PROVIDER_TIMEOUT].envelope_code
    retryable = True


class ProviderUnavailableError(ProviderFailure):
    """Outage or connection reset before any answer was produced."""

    gateway_code = GatewayErrorCode.PROVIDER_UNAVAILABLE
    status_code = GATEWAY_ERROR_SPECS[GatewayErrorCode.PROVIDER_UNAVAILABLE].http_status
    code = GATEWAY_ERROR_SPECS[GatewayErrorCode.PROVIDER_UNAVAILABLE].envelope_code
    retryable = True


class InternalProviderError(ProviderFailure):
    """The provider answered, but with something unusable (5xx, malformed,
    empty). Retrying is not forbidden, it just is not promised either."""

    gateway_code = GatewayErrorCode.INTERNAL_ERROR
    status_code = GATEWAY_ERROR_SPECS[GatewayErrorCode.INTERNAL_ERROR].http_status
    code = GATEWAY_ERROR_SPECS[GatewayErrorCode.INTERNAL_ERROR].envelope_code


#: One typed exception class per standard code — the adapter-side translation
#: table. Construction is ``(message, *, retry_after_seconds=...)``.
PROVIDER_ERROR_CLASSES: dict[GatewayErrorCode, type[ProviderFailure]] = {
    GatewayErrorCode.INVALID_REQUEST: InvalidRequestProviderError,
    GatewayErrorCode.UNAUTHORIZED: UnauthorizedProviderError,
    GatewayErrorCode.FORBIDDEN: ForbiddenProviderError,
    GatewayErrorCode.RATE_LIMITED: RateLimitedProviderError,
    GatewayErrorCode.PROVIDER_TIMEOUT: ProviderTimeoutError,
    GatewayErrorCode.PROVIDER_UNAVAILABLE: ProviderUnavailableError,
    GatewayErrorCode.INTERNAL_ERROR: InternalProviderError,
}

# ---------------------------------------------------------------------------
# Provider port
# ---------------------------------------------------------------------------


@runtime_checkable
class ChatProvider(Protocol):
    """The provider port. Adapters implement this; nothing else touches SDKs.

    Contract for every implementation:

    * success returns a fully normalized :class:`ProviderCompletion`;
    * failure raises a :class:`ProviderFailure` subclass, never a raw SDK or
      vendor-shaped exception;
    * the call must be cancellable — the service enforces the timeout.
    """

    @property
    def name(self) -> str:
        """Provider identity reported in normalized results (e.g. ``fake``)."""
        ...

    @property
    def default_model(self) -> str:
        """Model identity used/echoed when the request does not pin one."""
        ...

    async def complete(self, request: CompletionRequest) -> ProviderCompletion:
        """One completion. See the class docstring for the translation duty."""
        ...


__all__ = [
    "AUTO_MODEL",
    "CHAT_ALLOWED_ROLES",
    "ChatMessageView",
    "ChatProvider",
    "CompletionRequest",
    "FAKE_MODEL",
    "FAKE_PROVIDER",
    "ForbiddenProviderError",
    "GATEWAY_ERROR_SPECS",
    "GatewayErrorCode",
    "InternalProviderError",
    "InvalidRequestProviderError",
    "PROVIDER_ERROR_CLASSES",
    "ProviderCompletion",
    "ProviderFailure",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "RateLimitedProviderError",
    "UnauthorizedProviderError",
    "UsageKind",
]
