"""Sprint 1 1.A1.7 — minimal HITL pause/resume integration test.

Drives the new 6-phase graph through the gate1 → research → gate2 pause
sequence with stubbed phase + research nodes, asserting:

1. `interrupt()` fires at gate1_pause after intake completes.
2. `Command(resume={"action": "advance"})` resumes; the graph runs research
   fan-out (4 stubbed subagents) and pauses again at gate2_pause.
3. The dict-keyed `research_parts` accumulator is populated by all four
   stubbed scopes between the two interrupts (proves gate1→research wiring).

Phase + research nodes are stubbed via `monkeypatch` so the test exercises
LangGraph topology + checkpointer + interrupt semantics, not OpenAI.

Sprint 1 acceptance: `interrupt()` semantics + InMemorySaver compile-time
wiring. Full multi-gate UX (gate3 / gate4 / rerun-with-instructions /
halt path) is 4.A3 territory; the gate1↔gate2 round-trip here is
sufficient to prove the contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

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
    """Replacement for `make_phase_node` — emits an empty state delta.

    Sprint 1 phase-output integration into CaseState is not yet wired
    (Sprint 2 concern). The HITL contract only requires that the phase
    runs to completion so the gate that follows can fire `interrupt()`.
    """

    async def _node(_state: dict[str, Any]) -> dict[str, Any]:
        return {}

    _node.__name__ = f"stub_phase_{phase}"
    return _node


def _stub_research_factory(scope: str):
    """Replacement for `make_research_node` — emits one accumulator entry."""

    async def _node(_state: dict[str, Any]) -> dict[str, Any]:
        return {"research_parts": {scope: _stub_research_part(scope)}}

    _node.__name__ = f"stub_research_{scope}"
    return _node


def _initial_state() -> dict[str, Any]:
    return {
        "case": CaseState(case_id="00000000-0000-0000-0000-000000000abc"),
        "run_id": "run-hitl-1",
        "extra_instructions": {},
        "retry_counts": {},
        "halt": None,
        "mlflow_run_ids": {},
        "research_parts": {},
        "research_output": None,
        "is_resume": False,
        "start_agent": None,
    }


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
# Contract tests
# ---------------------------------------------------------------------------


async def test_intake_pauses_at_gate1(monkeypatch) -> None:
    """First invocation: intake runs → graph pauses at gate1_pause."""
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-gate1-pause"}}

    await compiled.ainvoke(_initial_state(), config)

    state = await compiled.aget_state(config)
    interrupted_tasks = [t for t in state.tasks if t.interrupts]
    assert interrupted_tasks, (
        f"Expected gate1 to interrupt; got tasks={[(t.name, t.interrupts) for t in state.tasks]}"
    )
    assert "gate1_pause" in {t.name for t in interrupted_tasks}, (
        f"Expected pause on gate1_pause; got {[t.name for t in interrupted_tasks]}"
    )


async def test_resume_with_advance_runs_research_and_pauses_at_gate2(
    monkeypatch,
) -> None:
    """Resuming gate1 with action=advance fans out research and pauses at gate2."""
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-gate1-advance"}}

    # Drive to gate1
    await compiled.ainvoke(_initial_state(), config)
    # Resume with advance — should fan out research and stop at gate2
    await compiled.ainvoke(Command(resume={"action": "advance"}), config)

    state = await compiled.aget_state(config)
    research_parts = state.values.get("research_parts") or {}
    assert set(research_parts.keys()) == {"evidence", "facts", "witnesses", "law"}, (
        "All four research subagents must run between gate1 and gate2; "
        f"got research_parts keys = {sorted(research_parts.keys())}"
    )

    interrupted_names = {t.name for t in state.tasks if t.interrupts}
    assert "gate2_pause" in interrupted_names, (
        f"Expected pause on gate2_pause; got interrupted_tasks={interrupted_names}"
    )
