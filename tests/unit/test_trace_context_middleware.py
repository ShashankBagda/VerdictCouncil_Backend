"""Trace-context middleware: reads active OTEL span and stashes trace_id (Sprint 2 2.C1.3)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider

from src.api.middleware.trace_context import TraceContextMiddleware


@pytest.fixture(autouse=True)
def _provider() -> None:
    """Ensure a real tracer provider is active so spans carry valid IDs."""
    trace.set_tracer_provider(TracerProvider())


def _build_app() -> FastAPI:
    app = FastAPI()
    FastAPIInstrumentor.instrument_app(app)
    app.add_middleware(TraceContextMiddleware)

    @app.get("/echo-trace")
    async def echo_trace(request: Request) -> dict[str, str | None]:
        return {"trace_id": getattr(request.state, "trace_id", None)}

    return app


def test_middleware_extracts_hex_trace_id() -> None:
    client = TestClient(_build_app())
    resp = client.get("/echo-trace")
    body = resp.json()
    assert resp.status_code == 200
    trace_id = body["trace_id"]
    assert trace_id is not None
    assert isinstance(trace_id, str)
    assert len(trace_id) == 32, "W3C trace ids are 32 lowercase hex chars"
    int(trace_id, 16)  # must parse as hex


def test_middleware_honors_inbound_traceparent() -> None:
    client = TestClient(_build_app())
    inbound = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    resp = client.get("/echo-trace", headers={"traceparent": inbound})
    body = resp.json()
    assert body["trace_id"] == "0af7651916cd43dd8448eb211c80319c"
