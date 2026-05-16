"""
Mission ID propagation — contextvars + Starlette middleware.

Pattern:
  1. Inbound request hits MissionMiddleware → reads X-Mission-ID → stores in
     a contextvar that lives for the request lifetime.
  2. Anywhere inside the request handler — including the SDK's outbound
     client wrapper — `current_mission_id()` returns the same value.
  3. The outbound client adds X-Mission-ID to every downstream call.

The contextvar pattern works correctly with asyncio because each task
inherits its parent's context. No global state.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response

_MISSION_ID: ContextVar[str | None] = ContextVar("aauth_mission_id", default=None)

MISSION_HEADER = "X-Mission-ID"


def current_mission_id() -> str | None:
    """Return the mission_id for the current request, or None."""
    return _MISSION_ID.get()


def set_mission_id(value: str | None) -> None:
    """
    Manually set the mission_id for the current context. Useful when the
    backend originates a mission and wants to seed the contextvar before
    making downstream calls.
    """
    _MISSION_ID.set(value)


class MissionMiddleware(BaseHTTPMiddleware):
    """
    Starlette/FastAPI middleware. Extracts X-Mission-ID from the inbound
    request and stores it for the duration of the request handler. Also
    echoes the value back on the response so callers can confirm
    propagation worked.
    """

    async def dispatch(
        self,
        request: StarletteRequest,
        call_next: Callable[[StarletteRequest], Awaitable[Response]],
    ) -> Response:
        mid = request.headers.get(MISSION_HEADER)
        token = _MISSION_ID.set(mid)
        try:
            response = await call_next(request)
            if mid:
                response.headers[MISSION_HEADER] = mid
            return response
        finally:
            _MISSION_ID.reset(token)
