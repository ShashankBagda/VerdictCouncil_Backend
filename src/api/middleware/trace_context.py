"""Trace-context middleware: stashes the active OTEL trace_id on `request.state`.

Sprint 2 task 2.C1.3. Runs after FastAPIInstrumentor (which honors any inbound
W3C `traceparent` header), so the active span here is either the propagated
upstream context or a fresh server span if none was supplied. We expose the
hex trace_id to downstream handlers — runner, SSE emitter, audit log writer —
via `request.state.trace_id` so they can stamp it on outgoing payloads.
"""

from __future__ import annotations

from opentelemetry import trace
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp


class TraceContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        request.state.trace_id = _current_trace_id_hex()
        return await call_next(request)


def _current_trace_id_hex() -> str | None:
    """Return the active span's trace id formatted as 32 lowercase hex chars.

    Returns None when no span is active or when the recorded trace id is the
    invalid sentinel (0). Callers tolerate absence so unit tests that don't
    install a tracer provider keep working.
    """
    span = trace.get_current_span()
    span_context = span.get_span_context()
    if not span_context.is_valid:
        return None
    return f"{span_context.trace_id:032x}"
