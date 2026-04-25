"""Sprint 1 1.A1.8 — pipeline smoke test.

Drives `GraphPipelineRunner.run(case_state)` end-to-end with stubbed
phase + research nodes through gate1 (where the new topology
interrupts). Asserts:

1. `ainvoke` is never called on the compiled graph (acceptance criterion
   for 1.A1.8). The runner streams through `stream_to_sse(...)` and
   reads the terminal state via `aget_state(config)`.
2. Terminal state shape is unchanged — `runner.run(...)` still returns
   a `CaseState` carrying the same `case_id` as the input.

Phase + research nodes are stubbed so the test exercises the runner
plumbing, not OpenAI. Sprint 1 acceptance scope; full real-LLM
integration is Sprint 2+.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from src.pipeline.graph.schemas import (
    EvidenceResearch,
    FactsResearch,
    LawResearch,
    PrecedentProvenance,
    ResearchPart,
    WitnessesResearch,
)
from src.shared.case_state import CaseState

pytestmark = pytest.mark.asyncio


def _stub_research_part(scope: str) -> ResearchPart:
    if scope == "evidence":
        return ResearchPart(
            scope="evidence",
            evidence=EvidenceResearch(evidence_items=[], credibility_scores={}),
        )
    if scope == "facts":
        return ResearchPart(scope="facts", facts=FactsResearch(facts=[], timeline=[]))
    if scope == "witnesses":
        return ResearchPart(
            scope="witnesses",
            witnesses=WitnessesResearch(witnesses=[], credibility={}),
        )
    if scope == "law":
        return ResearchPart(
            scope="law",
            law=LawResearch(
                legal_rules=[],
                precedents=[],
                precedent_source_metadata=PrecedentProvenance(
                    source="vector_store",
                    query="",
                    retrieved_at=datetime(2026, 4, 25, 0, 0, 0),
                ),
                legal_elements_checklist=[],
                suppressed_citations=[],
            ),
        )
    raise ValueError(f"unknown research scope: {scope!r}")


def _stub_phase_factory(phase: str):
    async def _node(_state: dict[str, Any]) -> dict[str, Any]:
        return {}

    _node.__name__ = f"stub_phase_{phase}"
    return _node


def _stub_research_factory(scope: str):
    async def _node(_state: dict[str, Any]) -> dict[str, Any]:
        return {"research_parts": {scope: _stub_research_part(scope)}}

    _node.__name__ = f"stub_research_{scope}"
    return _node


def _patch_factories(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.pipeline.graph.builder.make_phase_node",
        _stub_phase_factory,
    )
    monkeypatch.setattr(
        "src.pipeline.graph.builder.make_research_node",
        _stub_research_factory,
    )


# ---------------------------------------------------------------------------
# Acceptance — runner does not call ainvoke; terminal state shape unchanged
# ---------------------------------------------------------------------------


async def test_runner_run_does_not_call_ainvoke(monkeypatch) -> None:
    """1.A1.8 contract: the runner uses streaming, not `ainvoke`."""
    _patch_factories(monkeypatch)

    # Stub Redis publishers so the SSE-emitting middleware doesn't try to
    # talk to a real Redis instance. The smoke test only cares about runner
    # plumbing, not SSE side effects.
    monkeypatch.setattr(
        "src.pipeline.graph.middleware.sse_bridge.publish_agent_event",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "src.pipeline.graph.runner_stream_adapter.publish_progress",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "src.pipeline.graph.runner_stream_adapter.publish_agent_event",
        lambda *_a, **_k: None,
    )

    from src.pipeline.graph.runner import GraphPipelineRunner

    runner = GraphPipelineRunner(checkpointer=InMemorySaver())

    ainvoke_calls: list[Any] = []
    original_ainvoke = runner._graph.ainvoke

    async def _spy_ainvoke(*args, **kwargs):
        ainvoke_calls.append((args, kwargs))
        return await original_ainvoke(*args, **kwargs)

    monkeypatch.setattr(runner._graph, "ainvoke", _spy_ainvoke)

    case = CaseState(case_id="00000000-0000-0000-0000-000000000abc")
    result = await runner.run(case)

    assert ainvoke_calls == [], (
        f"runner._invoke must not call graph.ainvoke (1.A1.8); got {len(ainvoke_calls)} call(s)"
    )
    assert result.case_id == case.case_id, (
        f"terminal CaseState must carry the original case_id; got {result.case_id!r}"
    )
