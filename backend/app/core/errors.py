"""The single API error envelope and its exception types.

Every non-2xx response produced by the shell uses exactly this shape::

    {"code": "...", "message": "...", "request_id": "...", "retryable": bool}

``message`` is safe to show operators: handlers translate known failure kinds
(validation, auth, provider, dependency outage) into stable machine-readable
``code`` values and never embed stack traces, SQL, DSNs, or exception reprs.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import exceptions as starlette_exceptions
from starlette.requests import Request

REQUEST_ID_HEADER = "X-Request-ID"

# RequestValidationError.errors() may contain user-supplied input values;
# only these structural fields are safe to echo into the error details.
_SAFE_VALIDATION_FIELDS = ("loc", "type", "msg")


def error_envelope(
    code: str,
    message: str,
    request_id: str | None,
    retryable: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    """Build the standard error body. ``extra`` adds documented fields only."""
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request_id,
        "retryable": retryable,
    }
    payload.update(extra)
    return payload


class AppError(Exception):
    """Base class for domain/edge errors that map onto the standard envelope.

    Deliberately *not* an HTTPException subclass: FastAPI answers HTTPException
    with its own ``{"detail": ...}`` shape even when a handler is registered,
    so envelope-consistent errors must live outside that hierarchy.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    retryable: bool = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PayloadTooLargeError(AppError):
    """Request body exceeds ``API_MAX_REQUEST_BYTES``; reject before parsing."""

    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "payload_too_large"


class DependencyUnavailableError(AppError):
    """A configured runtime dependency (database, cache) cannot be reached."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "dependency_unavailable"
    retryable = True


def request_id_of(request: Request) -> str | None:
    """Correlation id assigned by the middleware for this request."""
    return getattr(request.state, "request_id", None)


def envelope_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
    **extra: Any,
) -> JSONResponse:
    """Render the error envelope with the correlation header echoed back."""
    request_id = request_id_of(request)
    return JSONResponse(
        status_code=status_code,
        content=error_envelope(code, message, request_id, retryable, **extra),
        headers={REQUEST_ID_HEADER: request_id} if request_id else None,
    )


async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    """Stable code from the exception class; operator-facing message only."""
    return envelope_response(request, exc.status_code, exc.code, exc.message, exc.retryable)


async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
    """Any framework-raised HTTPException (404 routing, 401/403 from later RBAC)."""
    return envelope_response(
        request,
        exc.status_code,
        f"http_{exc.status_code}",
        str(exc.detail),
        retryable=exc.status_code in (429, 502, 503, 504),
    )


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 with a sanitized field-level summary — never the raw submitted values."""
    details = [
        {key: error[key] for key in _SAFE_VALIDATION_FIELDS if key in error}
        for error in exc.errors()
    ]
    return envelope_response(
        request,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "validation_error",
        "Request failed schema validation.",
        retryable=False,
        details=details,
    )


async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort 500. The true error is logged by ServerErrorMiddleware;
    the client only ever receives the fixed generic message."""
    return envelope_response(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "An unexpected error occurred.",
        retryable=False,
    )


HANDLERS: dict[type[Any], Any] = {
    AppError: handle_app_error,
    RequestValidationError: handle_validation_error,
    HTTPException: handle_http_error,
    # Starlette's own HTTPException is the *parent* of FastAPI's and is what
    # the router raises for unmatched paths, so it needs the envelope too.
    starlette_exceptions.HTTPException: handle_http_error,
    Exception: handle_unexpected,
}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach every envelope handler to the application."""
    for exception_type, handler in HANDLERS.items():
        app.add_exception_handler(exception_type, handler)
