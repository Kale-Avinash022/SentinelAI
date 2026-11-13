"""HTTP contract tests for the Day 006 shell.

Covers the four acceptance-critical contracts:
1. health semantics (liveness vs. readiness) and dependency outage reporting;
2. the single error envelope with correlation id on every failure;
3. request id propagation (inbound echo, generation, response header);
4. consistent rejection of oversized/invalid input before expensive parsing.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

ENVELOPE_KEYS = {"code", "message", "request_id", "retryable"}


async def test_version_endpoint_success_contract(shell_client: Any) -> None:
    response = await shell_client.get("/api/v1/version")
    assert response.status_code == 200
    body = response.json()
    assert body["api_version"] == "v1"
    assert body["service_version"] == "0.1.0"
    assert body["environment"] == "test"
    assert response.headers["X-Request-ID"]


async def test_liveness_up_while_readiness_reports_outage(
    shell_client: Any, ready_probe: Any
) -> None:
    """The distinct-behavior contract: a dead dependency must never flip liveness."""
    ready_probe.db_ok = False
    ready_probe.redis_ok = False

    live = await shell_client.get("/health")
    assert live.status_code == 200
    assert live.json()["status"] == "alive"
    assert "checks" not in live.json()

    ready = await shell_client.get("/ready")
    assert ready.status_code == 503
    body = ready.json()
    assert ENVELOPE_KEYS <= body.keys()
    assert body["code"] == "dependency_unavailable"
    assert body["retryable"] is True
    assert sorted(body["failed_checks"]) == ["database", "redis"]
    assert ready.headers["X-Request-ID"] == body["request_id"]


async def test_readiness_success_lists_probe_results(shell_client: Any) -> None:
    response = await shell_client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert [check["ok"] for check in body["checks"]] == [True, True]


async def test_request_id_generated_when_absent(shell_client: Any) -> None:
    response = await shell_client.get("/health")
    request_id = response.headers["X-Request-ID"]
    assert len(request_id) == 32  # uuid4 hex


async def test_inbound_request_id_echoed(shell_client: Any) -> None:
    response = await shell_client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert response.headers["X-Request-ID"] == "trace-abc-123"
    assert response.json()["request_id"] == "trace-abc-123"


async def test_malformed_inbound_request_id_replaced(shell_client: Any) -> None:
    response = await shell_client.get("/health", headers={"X-Request-ID": "bad id;inject!"})
    assert response.headers["X-Request-ID"] != "bad id;inject!"
    assert len(response.headers["X-Request-ID"]) == 32


async def test_unknown_route_uses_error_envelope(shell_client: Any) -> None:
    response = await shell_client.get("/api/v1/not-implemented")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "http_404"
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert body["retryable"] is False


async def test_validation_failure_envelope_sanitized(shell_client: Any) -> None:
    secret_value = "s3cr3t-payload-must-not-echo"
    response = await shell_client.post(
        "/api/v1/echo",
        json={"message": "", "repeat": 99, "surprise": secret_value},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert body["retryable"] is False
    # Field pointers only — never raw submitted values.
    assert secret_value not in response.text
    locations = [tuple(detail["loc"]) for detail in body["details"]]
    assert ("body", "message") in locations


async def test_oversized_body_rejected_by_declared_content_length(shell_client: Any) -> None:
    response = await shell_client.post(
        "/api/v1/echo",
        content=b"x" * 2048,
        headers={
            "Content-Type": "application/json",
            "Content-Length": "2048",
        },
    )
    assert response.status_code == 413
    body = response.json()
    assert body["code"] == "payload_too_large"
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert "Traceback" not in response.text


async def test_oversized_body_rejected_when_chunked(shell_app: Any) -> None:
    """Lying/absent Content-Length: the counted byte cap inside receive fires."""
    seen: dict[str, Any] = {}

    async def counting_receive() -> MutableMapping[str, Any]:
        seen["called"] = True
        return {
            "type": "http.request",
            "body": b'{"message": "' + b"y" * 4096 + b'"}',
            "more_body": False,
        }

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.start":
            seen["status"] = message["status"]

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/v1/echo",
        "raw_path": b"/api/v1/echo",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000),
        "server": ("shell", 80),
        "root_path": "",
        "state": {},
    }
    await shell_app(scope, counting_receive, send)

    assert seen["called"] is True
    assert seen["status"] == 413


async def test_openapi_documents_the_shell_contract(shell_app: Any) -> None:
    schema = shell_app.openapi()
    assert "/health" in schema["paths"]
    assert "/ready" in schema["paths"]
    assert "/api/v1/version" in schema["paths"]
    assert "/api/v1/echo" in schema["paths"]
    assert "503" in schema["paths"]["/ready"]["get"]["responses"]
