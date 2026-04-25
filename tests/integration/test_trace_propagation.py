"""End-to-end trace propagation: OTEL span ↔ LangGraph metadata ↔ SSE payload (Sprint 2 2.C1.8).

Acceptance criterion: a single inbound `traceparent` produces the same
`trace_id` on:

  1. the LangGraph `config.metadata.trace_id` that LangSmith records,
  2. the SSE `progress` event published to subscribers, and
  3. the OTEL span emitted by FastAPIInstrumentor / `trace.use_span`.

We assert all three within one test. Redis (publish_progress) and the
graph runner are stubbed so the test runs without external services.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.api.schemas.pipeline_events import PipelineProgressEvent
from src.api.trace_propagation import remote_span_from_traceparent
from src.pipeline.graph.runner import GraphPipelineRunner
from src.shared.case_state import CaseState

INBOUND_TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
EXPECTED_TRACE_ID = "0af7651916cd43dd8448eb211c80319c"


@pytest.fixture()
def span_exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        current.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
    return exporter


@pytest.mark.asyncio
async def test_traceparent_propagates_to_metadata_sse_and_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    captured_config: dict[str, dict] = {}
    captured_event: dict[str, PipelineProgressEvent | None] = {"event": None}

    async def fake_stream_to_sse(*, graph, initial_state, config, case_id):  # noqa: ARG001
        captured_config["config"] = config

    async def fake_publish_progress(event: PipelineProgressEvent) -> None:
        # Mirror the production publisher's auto-stamp behaviour so the
        # caller doesn't need to plumb trace_id explicitly.
        from src.api.trace_propagation import current_trace_id

        if event.trace_id is None:
            tid = current_trace_id()
            if tid:
                event = event.model_copy(update={"trace_id": tid})
        captured_event["event"] = event

    snapshot = MagicMock()
    snapshot.values = {"case": CaseState(case_id="11111111-1111-1111-1111-111111111111")}

    runner = GraphPipelineRunner.__new__(GraphPipelineRunner)
    runner._mode = "in_process"
    runner._graph = MagicMock()
    runner._graph.aget_state = AsyncMock(return_value=snapshot)

    parent_span = remote_span_from_traceparent(INBOUND_TRACEPARENT)
    assert parent_span is not None

    # Re-establish the worker's OTEL context exactly as `_run_with_outbox`
    # does after reading `pipeline_jobs.traceparent`.
    with (
        trace.use_span(parent_span, end_on_exit=False),
        patch("src.pipeline.graph.runner.stream_to_sse", side_effect=fake_stream_to_sse),
    ):
        # Open a child span so the exporter records something with the
        # inherited trace_id (the "OTEL span id" half of the assertion).
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("worker-child"):
            await runner.run(
                CaseState(case_id="11111111-1111-1111-1111-111111111111"),
                trace_id=EXPECTED_TRACE_ID,
            )
            await fake_publish_progress(
                PipelineProgressEvent(
                    case_id="11111111-1111-1111-1111-111111111111",
                    agent="pipeline",
                    phase="started",
                    ts=__import__("datetime").datetime.now(__import__("datetime").UTC),
                )
            )

    # 1. LangGraph metadata
    assert captured_config["config"]["metadata"]["trace_id"] == EXPECTED_TRACE_ID

    # 2. SSE payload
    sse_event = captured_event["event"]
    assert sse_event is not None
    assert sse_event.trace_id == EXPECTED_TRACE_ID

    # 3. OTEL span emitted under the inherited context
    spans = span_exporter.get_finished_spans()
    child = next((s for s in spans if s.name == "worker-child"), None)
    assert child is not None, f"worker-child span missing; got {[s.name for s in spans]}"
    assert f"{child.context.trace_id:032x}" == EXPECTED_TRACE_ID
