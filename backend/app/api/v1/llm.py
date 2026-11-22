"""Chat gateway endpoints (Day 015).

``POST /api/v1/llm/chat`` is the brief's model/messages/metadata/options
contract, answered entirely through the normalized gateway types. The route is
deliberately thin: it validates and bounds the wire request, hands a normalized
message tuple to :class:`~app.gateway.service.ChatGateway`, and projects the
:class:`~app.gateway.contracts.ProviderCompletion` back onto a stable response.
No provider SDK, adapter or scenario choice is reachable from here — that is the
service/registry's job, seeded from settings rather than the request.

Security posture:

* **The tenant comes from the principal, never the body.** The organization is
  whatever the Day 012 dependency resolved; there is no ``organization_id``/
  ``provider``/``scenario`` field a caller can forge. Chat selection is a
  server-side gateway decision.
* **Matrix v1 has no ``execute:chat`` verb**, so this step gates on role and
  excludes the read-only Viewer (ADR-0004). A later matrix bump should replace
  the role check with a dedicated permission; that intent is recorded here so
  the check is not mistaken for the final design.
* **Provider failures render the standard envelope.** The typed
  :class:`~app.gateway.contracts.ProviderFailure` subclasses *are*
  :class:`~app.core.errors.AppError` subclasses, so the global handler emits
  ``{code, message, request_id, retryable}`` with the mapped status and never
  leaks a vendor body, traceback, or the fake's internals.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import Principal, authenticated_principal
from app.auth.errors import PermissionDeniedError
from app.core.errors import DependencyUnavailableError
from app.gateway.contracts import (
    AUTO_MODEL,
    CHAT_ALLOWED_ROLES,
    ChatMessageView,
    ProviderCompletion,
)
from app.gateway.service import ChatGateway

router = APIRouter(prefix="/api/v1/llm", tags=["llm"])

# ---------------------------------------------------------------------------
# Contract bounds (the step's "bound message count, body size, max_tokens,
# timeout" made explicit and reusable). Body size itself is capped earlier, by
# the pre-parse BodySizeLimitMiddleware; these are the *semantic* limits.
# ---------------------------------------------------------------------------

MAX_MESSAGES = 32
MAX_MESSAGE_CHARS = 8_000
MAX_METADATA_ITEMS = 16

MessageRole = Literal["system", "user", "assistant", "tool"]


async def require_chat_role(
    principal: Annotated[Principal, Depends(authenticated_principal)],
) -> Principal:
    """Authenticate, then reject the read-only Viewer from invoking the gateway.

    Substitutes for a dedicated ``execute:chat`` permission that matrix v1 does
    not yet define (see module docstring).
    """
    if principal.role.value not in CHAT_ALLOWED_ROLES:
        raise PermissionDeniedError("Your role may not invoke the chat gateway.")
    return principal


ChatDep = Annotated[Principal, Depends(require_chat_role)]


def get_chat_gateway(request: Request) -> ChatGateway:
    gateway = getattr(request.app.state, "chat_gateway", None)
    if gateway is None:
        # Fail closed like every prior store dependency: an unwired gateway is
        # an outage, never an unauthenticated free pass or a silent success.
        raise DependencyUnavailableError("LLM chat gateway is unavailable.")
    return gateway  # type: ignore[no-any-return]


GatewayDep = Annotated[ChatGateway, Depends(get_chat_gateway)]


# ---------------------------------------------------------------------------
# Request / response contracts
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """One turn of conversation on the wire. Content length is capped."""

    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatOptions(BaseModel):
    """Generation options. Every field is bounded; unknown options are refused
    so a caller cannot smuggle a vendor-specific knob past the contract."""

    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = Field(default=None, ge=1, le=32_000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    # A caller may ask for a shorter deadline, never a longer one than the
    # gateway's configured ceiling (the service clamps it).
    timeout_ms: int | None = Field(default=None, ge=250, le=120_000)


class ChatRequest(BaseModel):
    """The brief's chat body: model, messages, metadata, options.

    ``model`` defaults to the virtual ``sentinel-auto``; the gateway resolves a
    concrete provider/model, and the response reports which one answered.
    """

    model_config = ConfigDict(extra="forbid")

    model: str = Field(default=AUTO_MODEL, min_length=1, max_length=200)
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=MAX_METADATA_ITEMS)
    options: ChatOptions = Field(default_factory=ChatOptions)


class UsageResponse(BaseModel):
    """Token accounting with its truth label exposed, never implied."""

    kind: Literal["exact", "estimated", "unknown"]
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class ChatResponse(BaseModel):
    """Normalized completion — the same shape for every provider/fake."""

    object: Literal["chat.completion"] = "chat.completion"
    provider: str
    model: str
    content: str
    finish_reason: str
    usage: UsageResponse
    latency_ms: int
    request_id: str | None


def _to_views(messages: list[ChatMessage]) -> list[ChatMessageView]:
    return [ChatMessageView(role=m.role, content=m.content) for m in messages]


def _respond(result: ProviderCompletion, request_id: str | None) -> ChatResponse:
    usage = result.usage
    return ChatResponse(
        provider=result.provider,
        model=result.model,
        content=result.content,
        finish_reason=result.finish_reason,
        usage=UsageResponse(
            kind=usage.kind.value,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
        ),
        latency_ms=result.latency_ms,
        request_id=request_id,
    )


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Normalized chat completion against the gateway provider port",
    description=(
        "Runs one chat completion through the LLM gateway and returns the "
        "provider-agnostic result. Backed by deterministic fake providers; no "
        "paid call is made. Errors use the standard envelope."
    ),
    responses={
        429: {"description": "rate_limited — provider throttling; back off."},
        500: {"description": "internal_error — malformed/empty/unusable provider answer."},
        503: {"description": "provider_unavailable or dependency_unavailable."},
        504: {
            "description": "provider_timeout — deadline exceeded; ambiguous, do not blind-replay."
        },
    },
)
async def chat(
    payload: ChatRequest,
    principal: ChatDep,
    gateway: GatewayDep,
    request: Request,
) -> ChatResponse:
    options: dict[str, Any] = {
        key: value
        for key, value in {
            "temperature": payload.options.temperature,
            "top_p": payload.options.top_p,
        }.items()
        if value is not None
    }
    max_output = payload.options.max_tokens or 1_024
    result = await gateway.complete(
        messages=_to_views(payload.messages),
        model=payload.model,
        max_output_tokens=max_output,
        timeout_ms=payload.options.timeout_ms,
        trace_id=getattr(request.state, "request_id", None),
        options=options,
    )
    return _respond(result, getattr(request.state, "request_id", None))


__all__ = ["router"]
