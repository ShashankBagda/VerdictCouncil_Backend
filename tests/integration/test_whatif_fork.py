"""Sprint 4 4.A5.1 / 4.A5.4 — What-If LangGraph fork contract.

Locks the fork primitive against the real ``InMemorySaver`` + compiled
graph. Phase / research nodes are stubbed (same trick as the
4.A3 resume-driver tests) so the integration exercises the fork seam,
not OpenAI.

Acceptance covered here:

- New :func:`create_whatif_fork` returns a ``fork_thread_id`` that
  includes the judge_id (R-10 thread-key isolation hint).
- The fork seeds the modified ``CaseState`` via
  ``aupdate_state(fork_config, …, as_node="research_join")`` so the
  fork resumes at the gate2 pause (synthesis re-runs against the
  modified state on advance).
- ``Overwrite`` sentinel makes ``_merge_case`` replace the case rather
  than merge — the seed must land verbatim under the custom reducer.
- The fork's metadata stamps ``parent_run_id`` + ``parent_thread_id``
  for LangSmith trace navigation.
- Cross-judge isolation: the original thread's terminal state is
  unaffected by the fork; a fork created by judge A carries judge A's
  id in the thread_id (judge B's lookup with its own judge_id will not
  match — API-layer enforcement is layered on top in ``what_if.py``).
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
from src.pipeline.graph.state import Overwrite, _merge_case
from src.services.whatif.fork import (
    WhatIfModification,
    create_whatif_fork,
    drive_whatif_to_terminal,
)
from src.shared.case_state import (
    CaseState,
    EvidenceAnalysis,
    ExtractedFacts,
)

# ---------------------------------------------------------------------------
# Stubs (no OpenAI; same shape as test_resume_driver.py)
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
    monkeypatch.setattr("src.pipeline.graph.builder.make_phase_node", _stub_phase_factory)
    monkeypatch.setattr("src.pipeline.graph.builder.make_research_node", _stub_research_factory)


def _initial_state(case_id: str) -> dict[str, Any]:
    return {
        "case": CaseState(
            case_id=case_id,
            evidence_analysis=EvidenceAnalysis(
                evidence_items=[
                    {"id": "e1", "weight": "high"},
                    {"id": "e2", "weight": "medium"},
                ]
            ),
            extracted_facts=ExtractedFacts(facts=[{"id": "f1", "status": "agreed"}]),
        ),
        "run_id": f"orig-run-{case_id[-12:]}",
        "extra_instructions": {},
        "retry_counts": {},
        "halt": None,
        "research_parts": {},
        "research_output": None,
        "is_resume": False,
        "start_agent": None,
    }


async def _drive_original_to_terminal(compiled, case_id: str) -> str:
    """Run the stub pipeline end-to-end to populate the original thread."""
    config = {"configurable": {"thread_id": case_id}}
    await compiled.ainvoke(_initial_state(case_id), config)
    # gate1 → gate2 → gate3 → gate4 → END
    for _ in range(4):
        await compiled.ainvoke(Command(resume={"action": "advance"}), config)
    return case_id


# ---------------------------------------------------------------------------
# Overwrite sentinel — _merge_case
# ---------------------------------------------------------------------------


def test_overwrite_sentinel_replaces_case_in_reducer() -> None:
    """``_merge_case`` must short-circuit when update is wrapped in Overwrite.

    Without Overwrite, the reducer treats ``case`` updates with empty
    fields as "parallel branch didn't own this field" and keeps the
    base. For a fork that deliberately strips a field (e.g. a judge
    excluding all evidence), that masking is wrong — the seed must
    land verbatim. Overwrite signals "ignore base, take update."
    """
    base = CaseState(
        case_id="11111111-1111-1111-1111-111111111111",
        evidence_analysis=EvidenceAnalysis(evidence_items=[{"id": "e1", "weight": "high"}]),
    )
    # Update has empty evidence_analysis — under default merge rules
    # (base non-empty, update empty) the base wins. Overwrite flips it.
    replacement = CaseState(
        case_id="11111111-1111-1111-1111-111111111111",
        evidence_analysis=None,
    )

    merged = _merge_case(base, Overwrite(replacement))  # type: ignore[arg-type]

    assert merged.evidence_analysis is None, (
        "Overwrite must replace, not merge — base evidence should be discarded"
    )


def test_overwrite_sentinel_unwrapped_falls_back_to_default_merge() -> None:
    """Plain CaseState updates retain the parallel-safe merge semantics."""
    base = CaseState(
        case_id="11111111-1111-1111-1111-111111111111",
        evidence_analysis=EvidenceAnalysis(evidence_items=[{"id": "e1"}]),
    )
    update = CaseState(
        case_id="11111111-1111-1111-1111-111111111111",
        evidence_analysis=None,  # parallel branch didn't own this field
    )

    merged = _merge_case(base, update)

    assert merged.evidence_analysis == base.evidence_analysis, (
        "Without Overwrite, an empty update field must keep the base value"
    )


# ---------------------------------------------------------------------------
# create_whatif_fork — primitive contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_whatif_fork_returns_judge_scoped_thread_id(monkeypatch) -> None:
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    case_id = "22222222-2222-2222-2222-222222222222"
    judge_a = "judge-a"

    await _drive_original_to_terminal(compiled, case_id)

    fork_tid = await create_whatif_fork(
        graph=compiled,
        case_id=case_id,
        judge_id=judge_a,
        modifications=[
            WhatIfModification(
                modification_type="evidence_exclusion",
                payload={"evidence_id": "e1"},
            )
        ],
    )

    assert case_id in fork_tid, "fork thread_id must include the case_id prefix"
    assert "whatif" in fork_tid, "fork thread_id must include 'whatif' marker"
    assert judge_a in fork_tid, (
        "fork thread_id must include judge_id so a different judge cannot share keys"
    )


@pytest.mark.asyncio
async def test_create_whatif_fork_does_not_mutate_original_thread(monkeypatch) -> None:
    """R-10: original thread's terminal state must remain byte-stable."""
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    case_id = "33333333-3333-3333-3333-333333333333"

    await _drive_original_to_terminal(compiled, case_id)
    orig_config = {"configurable": {"thread_id": case_id}}
    before = await compiled.aget_state(orig_config)
    before_case_dump = before.values["case"].model_dump()

    await create_whatif_fork(
        graph=compiled,
        case_id=case_id,
        judge_id="judge-x",
        modifications=[
            WhatIfModification(
                modification_type="evidence_exclusion",
                payload={"evidence_id": "e1"},
            )
        ],
    )

    after = await compiled.aget_state(orig_config)
    assert after.values["case"].model_dump() == before_case_dump, (
        "fork must not mutate the original case_id thread"
    )


@pytest.mark.asyncio
async def test_create_whatif_fork_stamps_parent_lineage_in_metadata(monkeypatch) -> None:
    """Fork's checkpoint metadata must link back to the parent thread / run."""
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    case_id = "55555555-5555-5555-5555-555555555555"

    await _drive_original_to_terminal(compiled, case_id)
    orig_snap = await compiled.aget_state({"configurable": {"thread_id": case_id}})
    orig_run_id = orig_snap.values.get("run_id")

    fork_tid = await create_whatif_fork(
        graph=compiled,
        case_id=case_id,
        judge_id="judge-z",
        modifications=[
            WhatIfModification(
                modification_type="evidence_exclusion", payload={"evidence_id": "e2"}
            )
        ],
        parent_run_id=orig_run_id,
    )

    fork_snap = await compiled.aget_state({"configurable": {"thread_id": fork_tid}})
    metadata = fork_snap.metadata or {}
    # Lineage must land in checkpoint metadata — that is where the
    # LangSmith trace navigator reads it from. Asserting metadata
    # directly (no values-dict fallback) makes a misrouted stamp fail
    # loudly instead of silently passing via state leakage.
    assert metadata.get("parent_run_id") == orig_run_id, (
        f"fork must stamp parent_run_id in checkpoint metadata; "
        f"got metadata={metadata!r}, expected parent_run_id={orig_run_id!r}"
    )
    assert metadata.get("parent_thread_id") == case_id, (
        f"fork must stamp parent_thread_id in checkpoint metadata; "
        f"got metadata={metadata!r}, expected parent_thread_id={case_id!r}"
    )


@pytest.mark.asyncio
async def test_create_whatif_fork_pauses_at_gate2_for_modifier_to_advance(
    monkeypatch,
) -> None:
    """Fork seeds at research_join → next is gate2_pause.

    The judge's modification overrides research outputs; synthesis must
    re-run against the modified state. Seeding at research_join makes
    the gate2 pause fire so the caller can drive forward through
    Command(resume=advance).
    """
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    case_id = "66666666-6666-6666-6666-666666666666"

    await _drive_original_to_terminal(compiled, case_id)
    fork_tid = await create_whatif_fork(
        graph=compiled,
        case_id=case_id,
        judge_id="judge-q",
        modifications=[
            WhatIfModification(
                modification_type="evidence_exclusion",
                payload={"evidence_id": "e1"},
            )
        ],
    )

    fork_config = {"configurable": {"thread_id": fork_tid}}
    snap = await compiled.aget_state(fork_config)
    next_nodes = set(snap.next or ())
    assert "gate2_pause" in next_nodes or any(
        t.name == "gate2_pause" for t in (snap.tasks or [])
    ), f"fork must be paused at gate2_pause after seeding; got next={snap.next!r}"


# ---------------------------------------------------------------------------
# drive_whatif_to_terminal — auto-advance helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_whatif_to_terminal_advances_through_remaining_gates(
    monkeypatch,
) -> None:
    """Auto-advance helper drives the fork through every remaining gate to END."""
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    case_id = "77777777-7777-7777-7777-777777777777"

    await _drive_original_to_terminal(compiled, case_id)
    fork_tid = await create_whatif_fork(
        graph=compiled,
        case_id=case_id,
        judge_id="judge-w",
        modifications=[
            WhatIfModification(
                modification_type="evidence_exclusion",
                payload={"evidence_id": "e1"},
            )
        ],
    )

    await drive_whatif_to_terminal(graph=compiled, fork_thread_id=fork_tid)

    snap = await compiled.aget_state({"configurable": {"thread_id": fork_tid}})
    assert snap.next == (), (
        f"fork must reach END after drive_whatif_to_terminal; got next={snap.next!r}"
    )


# ---------------------------------------------------------------------------
# Cross-judge isolation (test_whatif_isolation.py — 4.A5.4)
# ---------------------------------------------------------------------------


def test_whatif_modification_rejects_unknown_type() -> None:
    """Modification types are gate-checked at construction so the fork
    primitive never silently re-runs the pipeline against an unknown
    payload shape."""
    with pytest.raises(ValueError, match="modification_type"):
        WhatIfModification(modification_type="not_a_real_type", payload={})  # type: ignore[arg-type]
