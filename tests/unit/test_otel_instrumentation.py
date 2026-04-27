"""Verifies FastAPIInstrumentor is wired so requests emit OTEL spans (Sprint 2 2.C1.2)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.api.app import create_app


@pytest.fixture()
def span_exporter() -> InMemorySpanExporter:
    """Install an in-memory OTEL exporter on the active tracer provider.

    OTEL's global provider is set-once at process scope; if a previous test
    already installed one we attach our exporter to it instead of trying
    (and silently failing) to override.
    """
    exporter = InMemorySpanExporter()
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        current.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
    return exporter


def test_request_emits_otel_span(span_exporter: InMemorySpanExporter) -> None:
    app = create_app()
    client = TestClient(app)

    resp = client.get("/api/v1/health/")
    assert resp.status_code in (200, 404, 405)

    spans = span_exporter.get_finished_spans()
    assert spans, "FastAPIInstrumentor should emit at least one span per request"
    # FastAPI instrumentor emits an HTTP server span; its kind is SERVER and
    # it carries http.* attributes regardless of route resolution.
    server_spans = [s for s in spans if s.attributes and s.attributes.get("http.method") == "GET"]
    assert server_spans, f"expected http server span; got {[s.name for s in spans]}"
