"""W3C `traceparent` helpers for crossing the API → worker outbox boundary.

The API request handler runs inside a FastAPIInstrumentor server span, but
that span dies the moment the response is sent. Cases are processed
asynchronously in arq workers, so the only way for the worker's OTEL span
(and downstream LangSmith run) to inherit the original trace is to persist
the W3C traceparent header onto the `pipeline_jobs` row at enqueue time and
re-establish context in the worker.

Sprint 2 task 2.C1.4.
"""

from __future__ import annotations

import re

from opentelemetry.trace import (
    NonRecordingSpan,
    Span,
    SpanContext,
    TraceFlags,
    TraceState,
)

# https://www.w3.org/TR/trace-context/#traceparent-header
_TRACEPARENT_RE = re.compile(
    r"^(?P<version>[0-9a-f]{2})-"
    r"(?P<trace_id>[0-9a-f]{32})-"
    r"(?P<span_id>[0-9a-f]{16})-"
    r"(?P<trace_flags>[0-9a-f]{2})$"
)


def current_trace_id() -> str | None:
    """Return the active span's trace id as 32 lowercase hex chars, or None.

    Returns None when no span is active or the span context is invalid
    (e.g., tests that don't install a tracer provider). Callers should
    treat absence as "no trace context" rather than failing.
    """
    from opentelemetry import trace

    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return None
    return f"{ctx.trace_id:032x}"


def format_w3c_traceparent(span: Span) -> str | None:
    """Render the active span as a W3C traceparent header value.

    Returns None if the span context is invalid (e.g., no tracer provider).
    """
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return None
    return f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{ctx.trace_flags:02x}"


def parse_traceparent(traceparent: str | None) -> dict[str, str]:
    """Return component fields, or `{}` if the header is missing/malformed."""
    if not traceparent:
        return {}
    match = _TRACEPARENT_RE.match(traceparent.strip())
    if not match:
        return {}
    return match.groupdict()


def span_context_from_traceparent(traceparent: str | None) -> SpanContext | None:
    """Build an OTEL `SpanContext` (remote) from a traceparent header.

    The returned context can be wrapped in a `NonRecordingSpan` and set
    as the worker's current span so the worker's emitted spans inherit
    the API request's trace_id.
    """
    parts = parse_traceparent(traceparent)
    if not parts:
        return None
    return SpanContext(
        trace_id=int(parts["trace_id"], 16),
        span_id=int(parts["span_id"], 16),
        is_remote=True,
        trace_flags=TraceFlags(int(parts["trace_flags"], 16)),
        trace_state=TraceState(),
    )


def remote_span_from_traceparent(traceparent: str | None) -> Span | None:
    """Wrap a remote SpanContext in a non-recording span ready for `use_span()`."""
    ctx = span_context_from_traceparent(traceparent)
    if ctx is None:
        return None
    return NonRecordingSpan(ctx)
