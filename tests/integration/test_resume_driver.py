"""Sprint 4 4.A3.5/4.A3.6 — drive_resume contract.

Locks the worker-side translation of a ``/respond`` job payload into a
LangGraph ``Command(resume=...)`` invocation against the real
``InMemorySaver``. Phase + research nodes are stubbed so the test
exercises the resume primitive itself, not OpenAI.

The unit tests live alongside the graph topology tests because they
need a compiled graph + saver to be meaningful — mocking those out
would let bugs in the actual integration slip through.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.pipeline.graph.resume import (
    build_resume_payload,
    drive_resume,
    find_pending_interrupt,
    gate_from_pause_node,
    has_pending_interrupt,
)
from src.pipeline.graph.schemas import (
    EvidenceResearch,
    FactsResearch,
    LawResearch,
    PrecedentProvenance,
    ResearchPart,
    WitnessesResearch,
)
from src.shared.case_state import CaseState


# ---------------------------------------------------------------------------
# Stubs (avoid OpenAI; same shape as test_interrupt_resume.py)
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
    raise ValueError(f"unknown scope: {scope!r}")


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
        "src.pipeline.graph.builder.make_phase_node", _stub_phase_factory
    )
    monkeypatch.setattr(
        "src.pipeline.graph.builder.make_research_node", _stub_research_factory
    )


def _initial_state(thread_id: str) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "node,expected",
    [
        ("gate1_pause", "gate1"),
        ("gate2_pause", "gate2"),
        ("gate3_pause", "gate3"),
        ("gate4_pause", "gate4"),
        ("gate1_apply", None),
        ("intake", None),
        ("gate10_pause", "gate10"),  # defensive — multi-digit accepted but never produced
    ],
)
def test_gate_from_pause_node(node: str, expected: str | None) -> None:
    assert gate_from_pause_node(node) == expected


def test_build_resume_payload_advance() -> None:
    out = build_resume_payload({"resume_action": "advance", "notes": "looks good"})
    assert out == {"action": "advance", "notes": "looks good"}


def test_build_resume_payload_rerun_with_field_corrections() -> None:
    out = build_resume_payload(
        {
            "resume_action": "rerun",
            "phase": "synthesis",
            "notes": "tighten Q3",
            "field_corrections": {"synthesis_output": {"judicial_questions": ["Q?"]}},
        }
    )
    assert out["action"] == "rerun"
    assert out["notes"] == "tighten Q3"
    assert out["field_corrections"]["synthesis_output"]["judicial_questions"] == ["Q?"]
    # The pure builder must not leak transport-only keys (phase/subagent) into
    # the resume payload — gate_apply doesn't read them.
    assert "phase" not in out
    assert "subagent" not in out


def test_build_resume_payload_halt() -> None:
    out = build_resume_payload({"resume_action": "halt", "notes": "withdrawn"})
    assert out == {"action": "halt", "notes": "withdrawn"}


@pytest.mark.parametrize("bad", [None, "send_back", "advance_now", ""])
def test_build_resume_payload_rejects_unknown_action(bad) -> None:
    with pytest.raises(ValueError):
        build_resume_payload({"resume_action": bad})


# ---------------------------------------------------------------------------
# Integration: drive_resume against a real compiled graph
# ---------------------------------------------------------------------------


async def _drive_to_gate(compiled, config, thread_id: str, target: int) -> None:
    await compiled.ainvoke(_initial_state(thread_id), config)
    for _ in range(1, target):
        await compiled.ainvoke(Command(resume={"action": "advance"}), config)


@pytest.mark.asyncio
async def test_drive_resume_advance_pauses_at_next_gate(monkeypatch) -> None:
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-drive-advance"}}

    await _drive_to_gate(compiled, config, "drive-advance", 1)
    outcome, gate, payload = await drive_resume(
        compiled, config, {"resume_action": "advance", "notes": "ok"}
    )
    assert outcome == "interrupt"
    assert gate == "gate2"
    assert payload is not None
    assert payload.get("gate") == "gate2"
    assert payload.get("actions") == ["advance", "rerun", "halt"]


@pytest.mark.asyncio
async def test_drive_resume_halt_terminates(monkeypatch) -> None:
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-drive-halt"}}

    await _drive_to_gate(compiled, config, "drive-halt", 2)
    outcome, gate, payload = await drive_resume(
        compiled,
        config,
        {"resume_action": "halt", "notes": "withdrawn"},
    )
    assert outcome == "terminal"
    assert gate is None
    assert payload is None
    state = await compiled.aget_state(config)
    assert state.values.get("halt", {}).get("notes") == "withdrawn"


@pytest.mark.asyncio
async def test_drive_resume_rerun_repauses_at_same_gate(monkeypatch) -> None:
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-drive-rerun"}}

    await _drive_to_gate(compiled, config, "drive-rerun", 3)
    outcome, gate, payload = await drive_resume(
        compiled,
        config,
        {
            "resume_action": "rerun",
            "phase": "synthesis",
            "notes": "rewrite Q3",
        },
    )
    assert outcome == "interrupt"
    assert gate == "gate3"
    assert payload is not None
    state = await compiled.aget_state(config)
    assert state.values.get("extra_instructions", {}).get("gate3") == "rewrite Q3"


@pytest.mark.asyncio
async def test_drive_resume_subagent_note_lands_under_subagent_key(
    monkeypatch,
) -> None:
    """Subagent + notes → gate_apply writes ``{subagent: note}`` directly.

    The dispatcher's Send payload propagates ``extra_instructions`` to
    every research subagent, but each scope only reads the entry keyed
    by its own scope name — so a note keyed by ``evidence`` is invisible
    to the other three scopes. That is the targeting contract.
    """
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-drive-subagent"}}

    await _drive_to_gate(compiled, config, "drive-subagent", 2)
    outcome, gate, _ = await drive_resume(
        compiled,
        config,
        {
            "resume_action": "rerun",
            "phase": "research",
            "subagent": "evidence",
            "notes": "weight matrix is wrong",
        },
    )
    assert outcome == "interrupt"
    assert gate == "gate2"
    state = await compiled.aget_state(config)
    extras = state.values.get("extra_instructions") or {}
    assert extras.get("evidence") == "weight matrix is wrong"
    # Subagent-targeted reruns deliberately don't write a generic
    # gate-keyed note — the corrective instruction is for one scope.
    assert "gate2" not in extras


def test_build_resume_payload_subagent_routes_notes_under_scope_key() -> None:
    out = build_resume_payload(
        {
            "resume_action": "rerun",
            "phase": "research",
            "subagent": "evidence",
            "notes": "weight matrix is wrong",
        }
    )
    # Notes routed as a dict so gate_apply writes them straight into
    # extra_instructions, scoped to the subagent.
    assert out == {
        "action": "rerun",
        "notes": {"evidence": "weight matrix is wrong"},
    }


def test_build_resume_payload_subagent_without_notes_omits_notes() -> None:
    out = build_resume_payload(
        {"resume_action": "rerun", "phase": "research", "subagent": "evidence"}
    )
    assert out == {"action": "rerun"}


@pytest.mark.asyncio
async def test_drive_resume_field_corrections_apply_to_state(monkeypatch) -> None:
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-drive-fields"}}

    await _drive_to_gate(compiled, config, "drive-fields", 3)
    corrections = {"synthesis_output": {"judicial_questions": ["Edited"]}}
    outcome, gate, _ = await drive_resume(
        compiled,
        config,
        {
            "resume_action": "rerun",
            "phase": "synthesis",
            "field_corrections": corrections,
        },
    )
    assert outcome == "interrupt"
    assert gate == "gate3"
    state = await compiled.aget_state(config)
    assert state.values.get("synthesis_output") == corrections["synthesis_output"]


@pytest.mark.asyncio
async def test_drive_resume_raises_when_no_pending_interrupt(monkeypatch) -> None:
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-drive-nopending"}}

    # No prior ainvoke — saver has nothing for this thread_id.
    with pytest.raises(RuntimeError, match="no pending interrupt"):
        await drive_resume(compiled, config, {"resume_action": "advance"})


# ---------------------------------------------------------------------------
# State inspectors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_has_pending_interrupt_after_initial_invoke(monkeypatch) -> None:
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-pending"}}

    assert await has_pending_interrupt(compiled, config) is False
    await compiled.ainvoke(_initial_state("pending"), config)
    assert await has_pending_interrupt(compiled, config) is True


@pytest.mark.asyncio
async def test_find_pending_interrupt_returns_gate_and_payload(monkeypatch) -> None:
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-find"}}

    await compiled.ainvoke(_initial_state("find"), config)
    found = await find_pending_interrupt(compiled, config)
    assert found is not None
    gate, payload = found
    assert gate == "gate1"
    assert payload.get("gate") == "gate1"
    assert payload.get("actions") == ["advance", "rerun", "halt"]
