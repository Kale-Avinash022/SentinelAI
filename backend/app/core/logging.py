"""Structured, metadata-only logging for the API edge.

Privacy contract (Day 006 / STRIDE threat model): log records may contain
request *metadata* — correlation id, method, resolved route template, status
code, latency, client IP — and NEVER request bodies, prompt/response content,
credentials, or query strings (which may carry sensitive parameters).

The record's ``extra`` dict is rendered as a single JSON line. Non-JSON-safe
values are stringified rather than crashing the log call.
"""

from __future__ import annotations

import json
import logging

_CONFIGURED = False

# Attributes promoted from a LogRecord's ``extra`` into the JSON payload.
_EXTRA_KEYS = (
    "request_id",
    "method",
    "route",
    "status_code",
    "duration_ms",
    "client_ip",
    "event",
    "environment",
    "exc",
)


class JsonFormatter(logging.Formatter):
    """Render each record as one JSON line containing only safe metadata."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in _EXTRA_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            # Only the exception class name leaks into logs — never the trace,
            # which may contain configuration values or connection strings.
            exc_type = record.exc_info[0]
            payload["exc_type"] = exc_type.__name__ if exc_type else None
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Attach the JSON formatter to the application logger hierarchy (idempotent)."""
    global _CONFIGURED
    root = logging.getLogger()
    if not _CONFIGURED:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.handlers = [handler]
        _CONFIGURED = True
    root.setLevel(level.upper())
    # Uvicorn's default access log would print raw request lines (with query
    # strings); the correlation middleware replaces it with structured events.
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False


def get_logger(name: str) -> logging.Logger:
    """Application logger bound to the configured JSON hierarchy."""
    return logging.getLogger(name)
