"""Sprint 1 1.A1.3 — runner stream adapter integration test.

`stream_to_sse(graph, initial_state, config, case_id)` drains
`graph.astream(stream_mode="custom")` and publishes each emitted chunk
to Redis via the existing publishers. Two invariants:

1. Cancellation propagates (asyncio.CancelledError lands on the caller).
2. Graph exceptions propagate (don't swallow runtime errors as silent
   end-of-stream).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytestmark = pytest.mark.asyncio


class _FakeGraph:
    """Stand-in for a compiled LangGraph that yields a fixed event sequence.

    `astream(stream_mode="custom")` returns an async generator whose items
    are whatever phase nodes write via `get_stream_writer()`. We mimic that
    here without booting the real graph.
    """

    def __init__(self, items: list[Any], raise_at: int | None = None) -> None:
        self._items = items
        self._raise_at = raise_at

    async def astream(self, _state, *, config=None, stream_mode: str = "values"):  # noqa: ANN001
        assert stream_mode == "custom", (
            f"adapter must use stream_mode='custom', got {stream_mode!r}"
        )
        for idx, item in enumerate(self._items):
            if self._raise_at is not None and idx == self._raise_at:
                raise RuntimeError("graph blew up")
            yield item


# ---------------------------------------------------------------------------
# Happy path — progress events go to publish_progress; agent events to
# publish_agent_event.
# ---------------------------------------------------------------------------


async def test_stream_to_sse_dispatches_by_kind(monkeypatch):
    from src.api.schemas.pipeline_events import PipelineProgressEvent
    from src.pipeline.graph import runner_stream_adapter as adapter

    progress_calls: list = []
    agent_calls: list = []

    async def _capture_progress(ev):
        progress_calls.append(ev)

    async def _capture_agent(case_id, ev):
        agent_calls.append((case_id, ev))

    monkeypatch.setattr(adapter, "publish_progress", _capture_progress)
    monkeypatch.setattr(adapter, "publish_agent_event", _capture_agent)

    case_id = "11111111-1111-1111-1111-111111111111"
    progress_event = PipelineProgressEvent(
        case_id=case_id,
        agent="evidence-analysis",
        phase="started",
        step=3,
        ts="2026-04-25T12:00:00Z",
    )
    agent_event = {
        "kind": "agent",
        "schema_version": 1,
        "case_id": case_id,
        "agent": "evidence-analysis",
        "event": "thinking",
        "content": "→ gpt-5",
        "ts": "2026-04-25T12:00:01Z",
    }

    graph = _FakeGraph([progress_event, agent_event])

    await adapter.stream_to_sse(
        graph=graph,
        initial_state={"foo": "bar"},
        config={"configurable": {"thread_id": case_id}},
        case_id=case_id,
    )

    assert len(progress_calls) == 1
    assert progress_calls[0].agent == "evidence-analysis"
    assert len(agent_calls) == 1
    assert agent_calls[0][0] == case_id
    assert agent_calls[0][1]["event"] == "thinking"


async def test_stream_to_sse_propagates_cancellation(monkeypatch):
    from src.pipeline.graph import runner_stream_adapter as adapter

    monkeypatch.setattr(adapter, "publish_progress", lambda *a, **k: None)
    monkeypatch.setattr(adapter, "publish_agent_event", lambda *a, **k: None)

    class _SlowGraph:
        async def astream(self, _state, *, config=None, stream_mode="values"):  # noqa: ANN001
            await asyncio.sleep(10)
            yield {"kind": "agent"}

    case_id = "11111111-1111-1111-1111-111111111111"
    task = asyncio.create_task(
        adapter.stream_to_sse(
            graph=_SlowGraph(),
            initial_state={},
            config={"configurable": {"thread_id": case_id}},
            case_id=case_id,
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_stream_to_sse_propagates_graph_exceptions(monkeypatch):
    from src.api.schemas.pipeline_events import PipelineProgressEvent
    from src.pipeline.graph import runner_stream_adapter as adapter

    progress_calls: list = []

    async def _capture_progress(ev):
        progress_calls.append(ev)

    monkeypatch.setattr(adapter, "publish_progress", _capture_progress)
    monkeypatch.setattr(adapter, "publish_agent_event", lambda *a, **k: None)

    case_id = "11111111-1111-1111-1111-111111111111"
    progress_event = PipelineProgressEvent(
        case_id=case_id,
        agent="evidence-analysis",
        phase="started",
        step=3,
        ts="2026-04-25T12:00:00Z",
    )

    # Yield one event, then blow up. The adapter must surface the exception
    # rather than ending the stream silently.
    graph = _FakeGraph([progress_event, {"kind": "agent"}], raise_at=1)

    with pytest.raises(RuntimeError, match="graph blew up"):
        await adapter.stream_to_sse(
            graph=graph,
            initial_state={},
            config={"configurable": {"thread_id": case_id}},
            case_id=case_id,
        )

    # The first event should still have been published before the exception.
    assert len(progress_calls) == 1


async def test_stream_to_sse_skips_unrecognized_chunks(monkeypatch):
    """Chunks without a recognizable `kind` must not crash the adapter --
    LangGraph may emit framework-internal items we don't know how to
    publish. The adapter should silently drop them and continue."""
    from src.pipeline.graph import runner_stream_adapter as adapter

    progress_calls: list = []

    async def _capture_progress(_ev):
        progress_calls.append(1)

    monkeypatch.setattr(adapter, "publish_progress", _capture_progress)
    monkeypatch.setattr(adapter, "publish_agent_event", lambda *a, **k: None)

    case_id = "11111111-1111-1111-1111-111111111111"
    graph = _FakeGraph([{"random": "junk"}, "string-chunk", 42])

    await adapter.stream_to_sse(
        graph=graph,
        initial_state={},
        config={"configurable": {"thread_id": case_id}},
        case_id=case_id,
    )

    assert progress_calls == [], "unrecognized chunks should not trigger publishes"
