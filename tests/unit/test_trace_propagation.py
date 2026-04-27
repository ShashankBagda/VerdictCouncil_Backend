"""Trace propagation across the worker boundary (Sprint 2 2.C1.4).

The integration / E2E tests for 2.C1.8 cover the API → worker → LangSmith
chain end-to-end. This module pins the units that 2.C1.4 introduces:

  * `format_w3c_traceparent(span)` round-trips with the OTEL parser
  * runner exposes `trace_id` on `config.metadata` so LangSmith filters work
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from src.api.trace_propagation import (
    format_w3c_traceparent,
    parse_traceparent,
    span_context_from_traceparent,
)


@pytest.fixture(autouse=True)
def _provider() -> None:
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(TracerProvider())


def test_format_w3c_traceparent_roundtrips_through_otel_parser() -> None:
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("test") as span:
        traceparent = format_w3c_traceparent(span)

    # OTEL's own propagator must accept the formatted header.
    carrier = {"traceparent": traceparent}
    ctx = TraceContextTextMapPropagator().extract(carrier)
    extracted_span = trace.get_current_span(ctx)
    extracted_ctx = extracted_span.get_span_context()
    assert extracted_ctx.is_valid
    assert f"{extracted_ctx.trace_id:032x}" == parse_traceparent(traceparent)["trace_id"]


def test_parse_traceparent_extracts_hex_ids() -> None:
    tp = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    parsed = parse_traceparent(tp)
    assert parsed == {
        "version": "00",
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
        "span_id": "b7ad6b7169203331",
        "trace_flags": "01",
    }


def test_parse_traceparent_returns_empty_for_invalid_input() -> None:
    assert parse_traceparent("not-a-traceparent") == {}
    assert parse_traceparent("") == {}


def test_span_context_from_traceparent_is_valid() -> None:
    tp = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    ctx = span_context_from_traceparent(tp)
    assert ctx is not None
    assert f"{ctx.trace_id:032x}" == "0af7651916cd43dd8448eb211c80319c"
    assert f"{ctx.span_id:016x}" == "b7ad6b7169203331"


def test_span_context_from_traceparent_handles_missing() -> None:
    assert span_context_from_traceparent(None) is None
    assert span_context_from_traceparent("garbage") is None


@pytest.mark.asyncio
async def test_runner_run_threads_trace_id_into_config_metadata() -> None:
    """`runner.run(case_state, trace_id=...)` must surface trace_id in LangSmith metadata."""
    from src.pipeline.graph.runner import GraphPipelineRunner
    from src.shared.case_state import CaseState

    captured: dict[str, dict] = {}

    async def fake_stream_to_sse(*, graph, initial_state, config, case_id):  # noqa: ARG001
        captured["config"] = config

    snapshot = MagicMock()
    snapshot.values = {"case": CaseState(case_id="11111111-1111-1111-1111-111111111111")}

    runner = GraphPipelineRunner.__new__(GraphPipelineRunner)
    runner._mode = "in_process"
    runner._graph = MagicMock()
    runner._graph.aget_state = AsyncMock(return_value=snapshot)

    with patch("src.pipeline.graph.runner.stream_to_sse", side_effect=fake_stream_to_sse):
        await runner.run(
            CaseState(case_id="11111111-1111-1111-1111-111111111111"),
            trace_id="0af7651916cd43dd8448eb211c80319c",
        )

    assert captured["config"]["metadata"]["trace_id"] == "0af7651916cd43dd8448eb211c80319c"
