"""The ``/api/v1`` router. Placeholder-free: only endpoints that are actually
implemented in the modular monolith are mounted here.

Future modules mount their own routers into this package as they land:
auth (Day 012), gateway (Days 15–21), metrics (Day 31), incidents (Day 41)…
Route handlers stay thin; domain logic lives in module services, never here.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app import __version__


class ApiVersionResponse(BaseModel):
    """Contract for ``GET /api/v1/version``."""

    api_version: str
    service_version: str
    environment: str


class EchoRequest(BaseModel):
    """Strict, bounded synthetic input for gateway contract tests.

    Extra keys are *forbidden* and values are length-capped: nothing here can
    smuggle oversized or unvalidated content into the handler.
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=256)
    repeat: int = Field(default=1, ge=1, le=8)


class EchoResponse(BaseModel):
    """Contract for ``POST /api/v1/echo``: the input echoed ``repeat`` times."""

    messages: list[str]
    request_id: str


router = APIRouter(prefix="/api/v1", tags=["v1"])


@router.get(
    "/version",
    response_model=ApiVersionResponse,
    summary="Active API version",
    description="Returns the versioned contract surface currently served.",
)
async def api_version(request: Request) -> ApiVersionResponse:
    return ApiVersionResponse(
        api_version="v1",
        service_version=__version__,
        environment=request.app.state.settings.environment,
    )


@router.post(
    "/echo",
    response_model=EchoResponse,
    summary="Deterministic echo (synthetic contract-test target)",
    responses={413: {"description": "Body exceeded the pre-parse size bound."}},
)
async def echo(request: Request, payload: EchoRequest) -> EchoResponse:
    """Pure, local, deterministic — the shell's synthetic provider stand-in.

    Oversized bodies never reach this handler: the body-size middleware has
    already answered 413 in the standard envelope.
    """
    return EchoResponse(
        messages=[payload.message] * payload.repeat,
        request_id=request.state.request_id,
    )
