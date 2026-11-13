"""Unit tests for Day 006 shell internals: settings, middleware, logging."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import pytest
from pydantic import ValidationError

TEST_SECRET = "test-secret-key-0123456789-0123456789-0123"

ReceiveFn = Callable[[], Awaitable[MutableMapping[str, Any]]]
SendFn = Callable[[MutableMapping[str, Any]], Awaitable[None]]


def _settings(**overrides: Any) -> Any:
    """Settings without env-file lookup; ``_env_file`` is init-only and mypy-invisible."""
    from app.core.settings import Settings

    kwargs: dict[str, Any] = {"_env_file": None}
    kwargs.update(overrides)
    return Settings(**kwargs)


# --- settings ---------------------------------------------------------------------------


def test_settings_reject_short_secret(minimal_env: None) -> None:
    with pytest.raises(ValidationError):
        _settings(secret_key="too-short")


def test_settings_reject_migration_identity_dsn(minimal_env: None) -> None:
    with pytest.raises(ValidationError, match="sentinel_app"):
        _settings(
            secret_key=TEST_SECRET,
            database_url="postgresql+asyncpg://sentinel_migrator:x@127.0.0.1:5433/sentinel_ai",
        )


def test_settings_reject_unknown_transport_and_log_level(minimal_env: None) -> None:
    with pytest.raises(ValidationError):
        _settings(secret_key=TEST_SECRET, async_transport="kafka")
    with pytest.raises(ValidationError):
        _settings(secret_key=TEST_SECRET, log_level="VERBOSE")


def test_secret_str_never_renders_plaintext(minimal_env: None) -> None:
    settings = _settings(secret_key=TEST_SECRET)
    assert TEST_SECRET not in repr(settings.secret_key)
    assert TEST_SECRET not in str(settings)


# --- body-size middleware -----------------------------------------------------------------


def _scope(content_length: str | None) -> MutableMapping[str, Any]:
    headers = [(b"content-type", b"application/json")]
    if content_length is not None:
        headers.append((b"content-length", content_length.encode()))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/anything",
        "raw_path": b"/anything",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
        "root_path": "",
        "state": {},
    }


async def _empty_receive() -> MutableMapping[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _collecting_send() -> tuple[SendFn, list[int]]:
    statuses: list[int] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.start":
            statuses.append(int(message["status"]))

    return send, statuses


async def test_body_limit_rejects_declared_length_without_calling_handler() -> None:
    from app.core.middleware import BodySizeLimitMiddleware

    reached_handler = False

    async def inner(scope: MutableMapping[str, Any], receive: ReceiveFn, send: SendFn) -> None:
        nonlocal reached_handler
        reached_handler = True

    send, statuses = _collecting_send()
    wrapped = BodySizeLimitMiddleware(inner, max_bytes=1024)
    await wrapped(_scope("2048"), _empty_receive, send)

    assert reached_handler is False
    assert statuses == [413]


async def test_body_limit_rejects_lying_content_length_after_counting() -> None:
    """Declared 10 bytes but streams more: the counted cap still answers 413."""
    from app.core.middleware import BodySizeLimitMiddleware

    async def streaming_receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": b"z" * 4096, "more_body": False}

    async def inner(scope: MutableMapping[str, Any], receive: ReceiveFn, send: SendFn) -> None:
        raise AssertionError("handler must not run for an oversized body")

    send, statuses = _collecting_send()
    wrapped = BodySizeLimitMiddleware(inner, max_bytes=1024)
    await wrapped(_scope("10"), streaming_receive, send)
    assert statuses == [413]


async def test_body_limit_replays_small_body_to_handler() -> None:
    from app.core.middleware import BodySizeLimitMiddleware

    replayed: list[bytes] = []

    async def inner(scope: MutableMapping[str, Any], receive: ReceiveFn, send: SendFn) -> None:
        message = await receive()
        replayed.append(message["body"])
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def small_receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": b"hello", "more_body": False}

    send, statuses = _collecting_send()
    wrapped = BodySizeLimitMiddleware(inner, max_bytes=1024)
    await wrapped(_scope("5"), small_receive, send)
    assert statuses == [200]
    assert replayed == [b"hello"]


# --- structured metadata-only logging -------------------------------------------------------


def test_json_formatter_emits_metadata_only() -> None:
    from app.core.logging import JsonFormatter

    record = logging.LogRecord(
        name="app.access",
        level=logging.INFO,
        pathname="<test>",
        lineno=1,
        msg="request completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "abc123"
    record.method = "POST"
    record.route = "/api/v1/echo"
    record.status_code = 200
    payload = json.loads(JsonFormatter().format(record))
    assert payload["request_id"] == "abc123"
    assert payload["route"] == "/api/v1/echo"
    assert payload["status_code"] == 200
    # No body, headers, or query string fields exist in the render contract.
    assert "body" not in payload
    assert "headers" not in payload
    assert "query" not in payload


def test_json_formatter_never_includes_traceback_text() -> None:
    import sys

    from app.core.logging import JsonFormatter

    try:
        raise ValueError("dsn=postgresql://sentinel_app:secret@host/db")
    except ValueError:
        record = logging.LogRecord(
            name="app",
            level=logging.ERROR,
            pathname="<test>",
            lineno=1,
            msg="boom",
            args=(),
            exc_info=sys.exc_info(),
        )
    rendered = JsonFormatter().format(record)
    assert "Traceback" not in rendered
    assert "secret@host" not in rendered
    assert json.loads(rendered)["exc_type"] == "ValueError"
