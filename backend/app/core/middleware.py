"""Edge middleware: request correlation, body-size bound, structured access log.

Layering (outermost first): CorrelationIdMiddleware → BodySizeLimitMiddleware.
The body bound therefore already sees an assigned request id, and every
response — success, rejection or error — carries ``X-Request-ID`` back.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import Message, Receive, Scope, Send

from app.core.errors import REQUEST_ID_HEADER, PayloadTooLargeError, envelope_response
from app.core.logging import get_logger

logger = get_logger("app.access")

# Client-supplied ids are accepted only when well-formed; otherwise replaced.
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")

# Request ids are plain strings; messages follow the ASGI MutableMapping shape.
MessageLike = MutableMapping[str, Any]
ReceiveLike = Callable[[], Awaitable[MessageLike]]
SendLike = Callable[[MessageLike], Awaitable[None]]
InnerApp = Callable[[Scope, ReceiveLike, SendLike], Awaitable[None]]


def _get_header(scope: Scope, name: str) -> str | None:
    headers: list[tuple[bytes, bytes]] = list(scope.get("headers", []))
    for key, value in headers:
        if key.decode("latin-1").lower() == name:
            return value.decode("latin-1")
    return None


class CorrelationIdMiddleware:
    """Assign/propagate a request id and emit one metadata-only access log line.

    Logs method, resolved route *template* (never the query string), status
    and duration. Bodies, headers and credentials are never touched.
    """

    def __init__(self, app: InnerApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = _get_header(scope, "x-request-id")
        request_id = (
            inbound if inbound and _REQUEST_ID_PATTERN.fullmatch(inbound) else uuid.uuid4().hex
        )
        scope["state"] = {**scope.get("state", {}), "request_id": request_id}

        status_code = 500
        started = time.perf_counter()

        async def send_with_headers(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_with_headers)

        route = scope.get("route")
        route_path = getattr(route, "path", None) if route is not None else None
        logger.info(
            "request completed",
            extra={
                "request_id": request_id,
                "method": scope.get("method"),
                "route": route_path or scope.get("path", ""),
                "status_code": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "client_ip": (scope.get("client") or (None,))[0],
            },
        )


class BodySizeLimitMiddleware:
    """Bound the request body *before* JSON/Pydantic parsing begins.

    Enforcement is entirely inside this middleware, which matters for two
    reasons: exceptions raised while reading the body would otherwise be
    rewritten by FastAPI into a generic 400, and user middleware sits outside
    the exception-handler layer so a raised error could not reach the envelope
    handlers at all. Instead the (bounded) body is buffered here and replayed
    to the inner application; over-limit requests are answered directly with
    the standard 413 envelope and never parsed.

    Works because ``max_request_bytes`` caps memory usage; oversized or
    lying ``Content-Length`` heads is caught without reading anything.
    """

    def __init__(self, app: InnerApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _get_header(scope, "content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                # Malformed Content-Length: fall through and count real bytes.
                pass

        chunks: list[bytes] = []
        consumed = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            chunk: bytes = message.get("body", b"")
            consumed += len(chunk)
            if consumed > self.max_bytes:
                await self._reject(scope, receive, send)
                return
            if chunk:
                chunks.append(chunk)
            more = bool(message.get("more_body", False))

        async def replay_receive() -> Message:
            if chunks:
                body = chunks.pop(0)
                return {"type": "http.request", "body": body, "more_body": bool(chunks)}
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer with the standard 413 envelope; the body is never parsed."""
        response = envelope_response(
            Request(scope),
            PayloadTooLargeError.status_code,
            PayloadTooLargeError.code,
            f"Request body exceeds the {self.max_bytes}-byte limit.",
            retryable=PayloadTooLargeError.retryable,
        )
        await response(scope, receive, send)
