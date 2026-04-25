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
async def test_create_whatif_fork_seeds_modified_state_in_fork_thread(monkeypatch) -> None:
    """The fork thread's seed must carry the judge's modification."""
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    case_id = "44444444-4444-4444-4444-444444444444"

    await _drive_original_to_terminal(compiled, case_id)

    fork_tid = await create_whatif_fork(
        graph=compiled,
        case_id=case_id,
        judge_id="judge-y",
        modifications=[
            WhatIfModification(
                modification_type="evidence_exclusion",
                payload={"evidence_id": "e1", "reason": "police bodycam excluded"},
            )
        ],
    )

    fork_config = {"configurable": {"thread_id": fork_tid}}
    snap = await compiled.aget_state(fork_config)
    fork_case = snap.values["case"]
    items = fork_case.evidence_analysis.evidence_items if fork_case.evidence_analysis else []
    excluded = [it for it in items if it.get("id") == "e1" and it.get("excluded")]
    assert excluded, "fork seed must carry the judge's evidence-exclusion modification"


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
    # The saver hangs run-level metadata under the per-checkpoint
    # `metadata` slot via the writer; both shapes are accepted.
    flat = {**metadata, **(metadata.get("writes") or {})}
    parent_run_id = (
        metadata.get("parent_run_id")
        or flat.get("parent_run_id")
        or fork_snap.values.get("parent_run_id")
    )
    parent_thread_id = (
        metadata.get("parent_thread_id")
        or flat.get("parent_thread_id")
        or fork_snap.values.get("parent_thread_id")
    )
    assert parent_run_id == orig_run_id, (
        f"fork must stamp parent_run_id; got {parent_run_id!r}, expected {orig_run_id!r}"
    )
    assert parent_thread_id == case_id, (
        f"fork must stamp parent_thread_id; got {parent_thread_id!r}, expected {case_id!r}"
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


@pytest.mark.asyncio
async def test_two_judges_get_distinct_fork_threads(monkeypatch) -> None:
    """Forks from two different judges land on disjoint thread_ids.

    Acceptance for R-10 cross-judge isolation: judge B's fork cannot
    collide with judge A's fork on the saver. The thread_id format is
    the structural guarantee; the API-layer ``created_by`` check
    (in ``what_if.py``) enforces that judge B cannot fetch judge A's
    fork even if they guess the thread_id.
    """
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    case_id = "88888888-8888-8888-8888-888888888888"

    await _drive_original_to_terminal(compiled, case_id)

    fork_a = await create_whatif_fork(
        graph=compiled,
        case_id=case_id,
        judge_id="judge-alpha",
        modifications=[
            WhatIfModification(
                modification_type="evidence_exclusion", payload={"evidence_id": "e1"}
            )
        ],
    )
    fork_b = await create_whatif_fork(
        graph=compiled,
        case_id=case_id,
        judge_id="judge-beta",
        modifications=[
            WhatIfModification(
                modification_type="evidence_exclusion", payload={"evidence_id": "e2"}
            )
        ],
    )

    assert fork_a != fork_b
    assert "judge-alpha" in fork_a and "judge-beta" not in fork_a
    assert "judge-beta" in fork_b and "judge-alpha" not in fork_b

    # Each fork's saver state is independent.
    snap_a = await compiled.aget_state({"configurable": {"thread_id": fork_a}})
    snap_b = await compiled.aget_state({"configurable": {"thread_id": fork_b}})

    items_a = (
        snap_a.values["case"].evidence_analysis.evidence_items
        if snap_a.values["case"].evidence_analysis
        else []
    )
    items_b = (
        snap_b.values["case"].evidence_analysis.evidence_items
        if snap_b.values["case"].evidence_analysis
        else []
    )
    assert any(it.get("id") == "e1" and it.get("excluded") for it in items_a)
    assert any(it.get("id") == "e2" and it.get("excluded") for it in items_b)
    assert not any(it.get("id") == "e2" and it.get("excluded") for it in items_a)
    assert not any(it.get("id") == "e1" and it.get("excluded") for it in items_b)


@pytest.mark.asyncio
async def test_fork_diverges_terminal_state_from_original(monkeypatch) -> None:
    """Driving the fork to END produces a different case state than the original.

    The stub pipeline isn't lossy enough to differ on its own — we
    seed a *visible* divergence (excluded evidence flag) and assert
    the fork's terminal CaseState reflects it while the original does
    not. That is the structural acceptance for "different terminal
    state" — actual verdict divergence requires a real LLM phase node
    and is covered by the manual smoke (4.C5b.5).
    """
    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    case_id = "99999999-9999-9999-9999-999999999999"

    await _drive_original_to_terminal(compiled, case_id)
    fork_tid = await create_whatif_fork(
        graph=compiled,
        case_id=case_id,
        judge_id="judge-diverge",
        modifications=[
            WhatIfModification(
                modification_type="fact_toggle",
                payload={"fact_id": "f1", "new_status": "disputed"},
            )
        ],
    )
    await drive_whatif_to_terminal(graph=compiled, fork_thread_id=fork_tid)

    orig_snap = await compiled.aget_state({"configurable": {"thread_id": case_id}})
    fork_snap = await compiled.aget_state({"configurable": {"thread_id": fork_tid}})

    orig_facts = orig_snap.values["case"].extracted_facts
    fork_facts = fork_snap.values["case"].extracted_facts
    assert orig_facts is not None and fork_facts is not None
    f1_orig = next(f for f in orig_facts.facts if f["id"] == "f1")
    f1_fork = next(f for f in fork_facts.facts if f["id"] == "f1")
    assert f1_orig["status"] == "agreed"
    assert f1_fork["status"] == "disputed"


def test_whatif_modification_rejects_unknown_type() -> None:
    """Modification types are gate-checked at construction so the fork
    primitive never silently re-runs the pipeline against an unknown
    payload shape."""
    with pytest.raises(ValueError, match="modification_type"):
        WhatIfModification(modification_type="not_a_real_type", payload={})  # type: ignore[arg-type]
