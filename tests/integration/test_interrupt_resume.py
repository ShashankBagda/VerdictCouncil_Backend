"""Sprint 4 4.A3.10 — full interrupt/resume contract across all four gates.

Drives the new 6-phase graph through every gate's advance / rerun / halt
path with `Command(resume=...)` against a real LangGraph
``InMemorySaver``. Phase + research nodes are stubbed via monkeypatch so
the test exercises topology + checkpointer + interrupt semantics, not
OpenAI.

The minimal Sprint-1 ``test_minimal_hitl.py`` only proved gate1 ↔ gate2
(intake → research → pause). This suite extends to:

- gate1 / gate2 / gate3 / gate4 each pause at the correct node
- ``advance`` from each gate routes to the expected next phase / END
- ``rerun`` from each gate routes back to the gate's own phase, then
  re-pauses at the same gate after the phase completes
- ``halt`` from any gate routes to ``terminal`` and the run ends
- ``Command(resume=None)`` does not desync the saver — the apply node
  defaults to ``advance`` when ``pending_action`` is unparseable, so a
  malformed judge response can never silently terminate

All four research subagents must emit a ``ResearchPart`` between gate1
and gate2 — the dispatcher's `Send` fan-out is the contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
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


# ---------------------------------------------------------------------------
# Stubs (avoid OpenAI; exercise topology only)
# ---------------------------------------------------------------------------


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
    """Empty phase delta — sufficient to drive the graph past each phase."""

    async def _node(_state: dict[str, Any]) -> dict[str, Any]:
        return {}

    _node.__name__ = f"stub_phase_{phase}"
    return _node


def _stub_research_factory(scope: str):
    async def _node(_state: dict[str, Any]) -> dict[str, Any]:
        return {"research_parts": {scope: _stub_research_part(scope)}}

    _node.__name__ = f"stub_research_{scope}"
    return _node


def _initial_state(thread_id: str) -> dict[str, Any]:
    # The terminal node validates case_id as a UUID, so derive a deterministic
    # hex suffix from the thread id rather than embedding the thread name itself.
    suffix = f"{abs(hash(thread_id)):012x}"[:12]
    return {
        "case": CaseState(case_id=f"00000000-0000-0000-0000-{suffix}"),
        "run_id": f"run-{thread_id}",
        "extra_instructions": {},
        "retry_counts": {},
        "halt": None,
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


def _interrupted_node_names(state) -> set[str]:
    return {t.name for t in state.tasks if t.interrupts}


async def _drive_to_gate(
    compiled: CompiledStateGraph, config: dict, thread_id: str, target_gate: int
) -> None:
    """Advance through gates until paused at ``target_gate``.

    Calls ``ainvoke`` with the initial state, then resumes with
    ``advance`` until the desired gate's pause node is the active
    interrupt task.
    """
    await compiled.ainvoke(_initial_state(thread_id), config)
    for _gate_num in range(1, target_gate):
        await compiled.ainvoke(Command(resume={"action": "advance"}), config)
    state = await compiled.aget_state(config)
    paused = _interrupted_node_names(state)
    expected = f"gate{target_gate}_pause"
    assert expected in paused, f"Expected pause at {expected}; got interrupted_tasks={paused}"


# ---------------------------------------------------------------------------
# Advance — every gate routes to the correct next phase
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "from_gate,expected_pause",
    [
        (1, "gate2_pause"),
        (2, "gate3_pause"),
        (3, "gate4_pause"),
    ],
)
async def test_advance_routes_to_next_gate(
    monkeypatch, from_gate: int, expected_pause: str
) -> None:
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": f"thread-advance-{from_gate}"}}

    await _drive_to_gate(compiled, config, f"advance-{from_gate}", from_gate)
    await compiled.ainvoke(Command(resume={"action": "advance"}), config)

    state = await compiled.aget_state(config)
    assert expected_pause in _interrupted_node_names(state), (
        f"After advancing gate{from_gate}, expected pause at {expected_pause}; "
        f"got interrupted_tasks={_interrupted_node_names(state)}"
    )


async def test_advance_from_gate4_terminates(monkeypatch) -> None:
    """Gate4 advance routes to END — no further interrupts, run is complete."""
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-gate4-end"}}

    await _drive_to_gate(compiled, config, "gate4-end", 4)
    await compiled.ainvoke(Command(resume={"action": "advance"}), config)

    state = await compiled.aget_state(config)
    assert not _interrupted_node_names(state), (
        f"Gate4 advance must reach END with no further interrupts; "
        f"got {_interrupted_node_names(state)}"
    )
    # `next` is empty when the graph has reached END.
    assert state.next == (), f"Graph must be at END; got next={state.next!r}"


async def test_research_fanout_runs_between_gate1_and_gate2(monkeypatch) -> None:
    """Locked from minimal-HITL — proves all four research subagents fire."""
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-fanout"}}

    await compiled.ainvoke(_initial_state("fanout"), config)
    await compiled.ainvoke(Command(resume={"action": "advance"}), config)

    state = await compiled.aget_state(config)
    research_parts = state.values.get("research_parts") or {}
    assert set(research_parts.keys()) == {"evidence", "facts", "witnesses", "law"}


# ---------------------------------------------------------------------------
# Rerun — every gate re-runs its own phase and re-pauses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("from_gate", [1, 2, 3, 4])
async def test_rerun_repauses_at_same_gate(monkeypatch, from_gate: int) -> None:
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": f"thread-rerun-{from_gate}"}}

    await _drive_to_gate(compiled, config, f"rerun-{from_gate}", from_gate)
    await compiled.ainvoke(
        Command(resume={"action": "rerun", "notes": "tighten this"}),
        config,
    )

    state = await compiled.aget_state(config)
    assert f"gate{from_gate}_pause" in _interrupted_node_names(state), (
        f"After rerun from gate{from_gate}, must re-pause at the same gate; "
        f"got interrupted_tasks={_interrupted_node_names(state)}"
    )

    # The judge's note must land in extra_instructions so the rerun phase
    # can read it. The gate apply node merges the dict using the gate
    # name as the agent key.
    extras = state.values.get("extra_instructions") or {}
    assert extras.get(f"gate{from_gate}") == "tighten this"


# ---------------------------------------------------------------------------
# Halt — every gate routes to terminal (Sprint 4 4.A3.12 cancellation)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("from_gate", [1, 2, 3, 4])
async def test_halt_terminates_run(monkeypatch, from_gate: int) -> None:
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": f"thread-halt-{from_gate}"}}

    await _drive_to_gate(compiled, config, f"halt-{from_gate}", from_gate)
    await compiled.ainvoke(
        Command(resume={"action": "halt", "notes": "case withdrawn"}),
        config,
    )

    state = await compiled.aget_state(config)
    assert not _interrupted_node_names(state), (
        f"Halt must reach END with no further interrupts; got {_interrupted_node_names(state)}"
    )
    assert state.next == (), f"Graph must be at END; got next={state.next!r}"

    halt = state.values.get("halt")
    assert halt is not None, "Halt action must populate the `halt` slot"
    assert halt.get("reason") == "judge_halt"
    assert halt.get("gate") == f"gate{from_gate}"
    assert halt.get("notes") == "case withdrawn"


# ---------------------------------------------------------------------------
# Field corrections — gate3 inline state edits applied with rerun
# ---------------------------------------------------------------------------


async def test_rerun_with_field_corrections_writes_state_slot(monkeypatch) -> None:
    """Gate3 judicial-question inline edits land in synthesis_output before rerun."""
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-fieldcorrect"}}

    await _drive_to_gate(compiled, config, "fieldcorrect", 3)
    corrections = {"synthesis_output": {"judicial_questions": ["Edited Q1?", "Edited Q2?"]}}
    await compiled.ainvoke(
        Command(
            resume={
                "action": "rerun",
                "notes": "rewrite questions",
                "field_corrections": corrections,
            }
        ),
        config,
    )

    state = await compiled.aget_state(config)
    syn = state.values.get("synthesis_output")
    # Stub phase factory writes nothing to synthesis_output, so the only
    # source of the corrected value is the gate apply node's update.
    assert syn == corrections["synthesis_output"], (
        f"field_corrections must land in synthesis_output; got {syn!r}"
    )
    assert "gate3_pause" in _interrupted_node_names(state)


# ---------------------------------------------------------------------------
# Defensive — malformed pending_action defaults to advance, never silently halts
# ---------------------------------------------------------------------------


async def test_unparseable_resume_defaults_to_advance(monkeypatch) -> None:
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-malformed"}}

    await _drive_to_gate(compiled, config, "malformed", 1)
    # Resume with an integer — neither a dict nor a known action string.
    await compiled.ainvoke(Command(resume=42), config)

    state = await compiled.aget_state(config)
    # Default `advance` falls through to research → gate2.
    assert "gate2_pause" in _interrupted_node_names(state), (
        f"Malformed resume must default to advance; got {_interrupted_node_names(state)}"
    )
