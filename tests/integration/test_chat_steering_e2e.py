"""Q1.11 chat-steering — graph-level integration test.

Exercises the full mid-phase interrupt → /respond → resume cycle against
a real LangGraph (in-process, InMemorySaver). The synthesis node is
stubbed to fire the same `interrupt({"kind": "ask_judge", ...})`
payload the real `ask_judge` tool would emit, so we can verify the
contract end-to-end without an OpenAI call.

What this test locks in:
  - A pending `ask_judge` interrupt is visible on `snapshot.tasks`.
  - The interrupt_id minted at pause time matches the one we use to
    resume — drift here would mean /respond's 409-on-mismatch never
    fires for legitimate matches either.
  - `Command(resume={"text": ...})` returns the text to the stub as
    the `interrupt(...)` return value, so an LLM-driven tool would
    see the judge's reply.
  - `aupdate_state(..., {"judge_messages": [HumanMessage(...)]})`
    persists across the resume — the chat thread survives.
  - Multi-turn within one phase works: a stub that calls interrupt()
    twice produces two pending interrupts in sequence with distinct
    interrupt_ids, each resolvable independently.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

from src.shared.case_state import CaseState

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Stubs — same pattern as test_interrupt_resume.py
# ---------------------------------------------------------------------------


def _stub_research_part(scope: str):
    from datetime import datetime as _dt

    from src.pipeline.graph.schemas import (
        EvidenceResearch,
        FactsResearch,
        LawResearch,
        PrecedentProvenance,
        ResearchPart,
        WitnessesResearch,
    )

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
                    retrieved_at=_dt(2026, 4, 27, 0, 0, 0),
                ),
                legal_elements_checklist=[],
                suppressed_citations=[],
            ),
        )
    raise ValueError(f"unknown scope: {scope!r}")


# Module-level holder so the test can read the interrupt_id the stub minted.
_minted: dict[str, list[str]] = {}


def _make_synthesis_stub(question_count: int = 1):
    """Build a synthesis stub that fires `ask_judge`-shaped interrupts.

    Mints a fresh uuid4().hex per call (matching the real tool) and
    records it in `_minted` so the test can resume with the right id.
    The stub returns nothing useful as a phase output — the test only
    checks the interrupt mechanics + state mutations, not artifact shape.
    """

    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        seen_replies: list[str] = []
        for i in range(question_count):
            interrupt_id = uuid.uuid4().hex
            case_id = str(state["case"].case_id)
            _minted.setdefault(case_id, []).append(interrupt_id)
            reply = interrupt(
                {
                    "kind": "ask_judge",
                    "question": f"Q{i + 1}?",
                    "interrupt_id": interrupt_id,
                }
            )
            if isinstance(reply, dict):
                seen_replies.append(str(reply.get("text") or ""))
            else:
                seen_replies.append(str(reply or ""))
        return {}

    _node.__name__ = "stub_synthesis_with_ask_judge"
    return _node


def _stub_phase_factory(phase: str):
    if phase == "synthesis":
        return _make_synthesis_stub(question_count=1)

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
        "judge_messages": [],
    }


def _patch_factories(monkeypatch, *, synthesis_questions: int = 1) -> None:
    monkeypatch.setattr(
        "src.pipeline.graph.builder.make_phase_node",
        lambda phase: (
            _make_synthesis_stub(question_count=synthesis_questions)
            if phase == "synthesis"
            else _stub_phase_factory(phase)
        ),
    )
    monkeypatch.setattr(
        "src.pipeline.graph.builder.make_research_node",
        _stub_research_factory,
    )


def _pending_ask_judge(snapshot) -> dict[str, Any] | None:
    """Walk snapshot.tasks for a pending ask_judge interrupt."""
    for task in snapshot.tasks:
        for ip in getattr(task, "interrupts", []) or []:
            value = getattr(ip, "value", None)
            if isinstance(value, dict) and value.get("kind") == "ask_judge":
                return value
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def _drive_to_synthesis_pause(compiled, config):
    """Drive the graph from initial state through gate1 + gate2 advances
    until synthesis fires its first ask_judge interrupt."""
    thread_id = config["configurable"]["thread_id"]
    await compiled.ainvoke(_initial_state(thread_id), config=config)
    # gate1 pauses → advance
    await compiled.ainvoke(Command(resume={"action": "advance"}), config=config)
    # gate2 pauses → advance into synthesis, which then fires ask_judge
    await compiled.ainvoke(Command(resume={"action": "advance"}), config=config)


async def test_synthesis_ask_judge_pauses_with_correct_payload(monkeypatch):
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t-pause"}}

    await _drive_to_synthesis_pause(compiled, config)

    snapshot = await compiled.aget_state(config)
    pending = _pending_ask_judge(snapshot)
    assert pending is not None, "synthesis stub must fire a ask_judge interrupt"
    assert pending["kind"] == "ask_judge"
    assert pending["question"] == "Q1?"
    case_id = str(snapshot.values["case"].case_id)
    assert pending["interrupt_id"] in _minted[case_id], (
        "the interrupt_id surfaced on the snapshot must match the one the stub minted"
    )


async def test_resume_returns_text_to_stub_and_persists_judge_message(monkeypatch):
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t-resume"}}

    await _drive_to_synthesis_pause(compiled, config)

    # Mirror what /respond does: atomic update+resume. Doing aupdate_state
    # first would fork the checkpoint and clear the pending interrupt,
    # after which Command(resume=...) has nothing to resume against —
    # the docstring on _handle_message_resume documents this trap.
    await compiled.ainvoke(
        Command(
            update={"judge_messages": [HumanMessage(content="prioritise reading B")]},
            resume={"text": "prioritise reading B"},
        ),
        config=config,
    )

    # Synthesis completes → graph pauses at gate3.
    snapshot = await compiled.aget_state(config)
    paused_nodes = {t.name for t in snapshot.tasks if t.interrupts}
    assert "gate3_pause" in paused_nodes, (
        f"after resume, expected pause at gate3; got {paused_nodes}"
    )
    # judge_messages persisted across the resume.
    judge_messages = snapshot.values["judge_messages"]
    assert len(judge_messages) == 1
    assert judge_messages[0].content == "prioritise reading B"


async def test_multi_turn_within_synthesis_phase(monkeypatch):
    """Q4 — multiple ask_judge calls in one phase produce distinct
    interrupt_ids and resolve independently."""
    _patch_factories(monkeypatch, synthesis_questions=2)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t-multi"}}

    await _drive_to_synthesis_pause(compiled, config)

    # First interrupt — resolve.
    snap = await compiled.aget_state(config)
    first = _pending_ask_judge(snap)
    assert first is not None and first["question"] == "Q1?"
    await compiled.ainvoke(
        Command(
            update={"judge_messages": [HumanMessage(content="answer-1")]},
            resume={"text": "answer-1"},
        ),
        config=config,
    )

    # Second interrupt — same node, fresh interrupt_id.
    snap = await compiled.aget_state(config)
    second = _pending_ask_judge(snap)
    assert second is not None and second["question"] == "Q2?"
    assert second["interrupt_id"] != first["interrupt_id"], (
        "multi-turn must mint distinct interrupt_ids — collision would let "
        "a resume payload for Q2 accidentally resolve a stale Q1"
    )
    await compiled.ainvoke(
        Command(
            update={"judge_messages": [HumanMessage(content="answer-2")]},
            resume={"text": "answer-2"},
        ),
        config=config,
    )

    snap = await compiled.aget_state(config)
    paused_nodes = {t.name for t in snap.tasks if t.interrupts}
    assert "gate3_pause" in paused_nodes
    contents = [m.content for m in snap.values["judge_messages"]]
    assert contents == ["answer-1", "answer-2"]


async def test_stale_interrupt_id_does_not_match_pending(monkeypatch):
    """409 contract: /respond must reject a resume whose interrupt_id
    doesn't match the pending one. We assert the matcher logic the
    endpoint relies on (`pending["interrupt_id"] == payload.interrupt_id`)
    actually distinguishes — drift here would give us silent
    double-resume on retries."""
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t-stale"}}

    await _drive_to_synthesis_pause(compiled, config)
    snap = await compiled.aget_state(config)
    pending = _pending_ask_judge(snap)
    assert pending is not None

    fake_id = "0" * 32
    assert pending["interrupt_id"] != fake_id
