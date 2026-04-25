"""Trace-context middleware: stashes the active OTEL trace_id on `request.state`.

Sprint 2 task 2.C1.3. Runs after FastAPIInstrumentor (which honors any inbound
W3C `traceparent` header), so the active span here is either the propagated
upstream context or a fresh server span if none was supplied. We expose the
hex trace_id to downstream handlers — runner, SSE emitter, audit log writer —
via `request.state.trace_id` so they can stamp it on outgoing payloads.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from src.api.trace_propagation import current_trace_id


class TraceContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        request.state.trace_id = current_trace_id()
        return await call_next(request)
