"""Sprint 4 4.A5.2 — fork-driven stability scoring.

Two-tier coverage:

1. Pure unit tests for ``classify()`` thresholds and
   ``identify_perturbations()`` discovery — these are the regression-prone
   bits that don't need a saver.
2. One integration test using ``InMemorySaver`` plus a divergence-aware
   synthesis stub so the per-fork verdict actually differs from the
   baseline. This locks the end-to-end shape of
   ``compute_stability_score`` against the real fork primitive.
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
from src.services.whatif.stability import (
    classify,
    compute_stability_score,
    identify_perturbations,
)
from src.shared.case_state import (
    CaseState,
    EvidenceAnalysis,
    ExtractedFacts,
    HearingAnalysis,
)

# ---------------------------------------------------------------------------
# classify() — pure threshold mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100, "stable"),
        (90, "stable"),
        (85, "stable"),
        (84, "moderately_sensitive"),
        (75, "moderately_sensitive"),
        (60, "moderately_sensitive"),
        (59, "highly_sensitive"),
        (25, "highly_sensitive"),
        (0, "highly_sensitive"),
    ],
)
def test_classify_thresholds(score: int, expected: str) -> None:
    """Classification bands at 85 / 60 boundaries.

    Boundary scores belong to the higher band — a case sitting exactly
    at 85 reads as ``stable``, not ``moderately_sensitive``. Same logic
    at 60.
    """
    assert classify(score) == expected


# ---------------------------------------------------------------------------
# identify_perturbations() — perturbation discovery
# ---------------------------------------------------------------------------


def _baseline_case() -> CaseState:
    """Two facts (one agreed, one disputed) + two non-excluded evidence items."""
    return CaseState(
        case_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        extracted_facts=ExtractedFacts(
            facts=[
                {"id": "f-1", "status": "agreed"},
                {"id": "f-2", "status": "disputed"},
            ]
        ),
        evidence_analysis=EvidenceAnalysis(
            evidence_items=[
                {"id": "e-1"},
                {"id": "e-2"},
            ]
        ),
    )


def test_identify_perturbations_returns_facts_and_evidence() -> None:
    perturbations = identify_perturbations(_baseline_case(), n=10)

    types = [p["modification_type"] for p in perturbations]
    assert types.count("fact_toggle") == 2
    assert types.count("evidence_exclusion") == 2

    f1 = next(p for p in perturbations if p["payload"].get("fact_id") == "f-1")
    assert f1["payload"]["new_status"] == "disputed"  # flips agreed → disputed
    f2 = next(p for p in perturbations if p["payload"].get("fact_id") == "f-2")
    assert f2["payload"]["new_status"] == "agreed"  # flips disputed → agreed


def test_identify_perturbations_caps_at_n() -> None:
    perturbations = identify_perturbations(_baseline_case(), n=2)
    assert len(perturbations) == 2


def test_identify_perturbations_skips_already_excluded_evidence() -> None:
    """An evidence item that is already excluded is not a meaningful perturbation."""
    case = CaseState(
        case_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        evidence_analysis=EvidenceAnalysis(
            evidence_items=[
                {"id": "e-1", "excluded": True},
                {"id": "e-2"},
            ]
        ),
    )
    perturbations = identify_perturbations(case, n=10)
    ids = [p["payload"]["evidence_id"] for p in perturbations]
    assert "e-1" not in ids
    assert "e-2" in ids


# ---------------------------------------------------------------------------
# compute_stability_score — empty perturbation set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_stability_score_returns_perfect_when_no_perturbations() -> None:
    """No perturbable inputs → vacuously stable.

    A case with no facts and no evidence cannot be perturbed, so the
    contract is to short-circuit at score=100 / classification=stable
    without touching the graph.
    """
    case = CaseState(case_id="cccccccc-cccc-cccc-cccc-cccccccccccc")

    # Sentinel graph — should never be called because there are zero
    # perturbations to run forks for. Any attribute access blows up
    # loudly so a regression that re-introduces a graph call here fails.
    class _ExplodingGraph:
        def __getattr__(self, item: str) -> Any:  # noqa: ARG002
            raise AssertionError("graph must not be touched when no perturbations exist")

    result = await compute_stability_score(
        graph=_ExplodingGraph(),  # type: ignore[arg-type]
        case_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        case_state=case,
        n=5,
        fork_judge_id="stab-test",
    )

    assert result == {
        "score": 100,
        "classification": "stable",
        "perturbation_count": 0,
        "perturbations_held": 0,
        "details": [],
    }


# ---------------------------------------------------------------------------
# Integration test — fork primitive end-to-end with divergence-aware stub
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


def _make_divergence_aware_phase_factory():
    """Phase factory whose synthesis node assigns a verdict from the case shape.

    - Excluded evidence → ``not_liable`` (so evidence_exclusion forks
      flip relative to the unmodified baseline).
    - No exclusions → ``liable`` (the baseline verdict and what
      fact_toggle forks return — fact toggles do not change the verdict
      under this stub, so they "hold").

    This is enough to exercise both ``verdict_held`` branches of
    ``compute_stability_score`` against a real saver.
    """

    def factory(phase: str):
        async def _node(state: dict[str, Any]) -> dict[str, Any]:
            if phase != "synthesis":
                return {}
            case: CaseState = state["case"]
            excluded = False
            if case.evidence_analysis:
                excluded = any(
                    isinstance(e, dict) and e.get("excluded")
                    for e in case.evidence_analysis.evidence_items
                )
            verdict = "not_liable" if excluded else "liable"
            new_case = case.model_copy(
                update={
                    "hearing_analysis": HearingAnalysis(
                        preliminary_conclusion=verdict,
                        confidence_score=80,
                    )
                }
            )
            return {"case": new_case}

        _node.__name__ = f"stub_phase_{phase}"
        return _node

    return factory


def _stub_research_factory(scope: str):
    async def _node(_state: dict[str, Any]) -> dict[str, Any]:
        return {"research_parts": {scope: _stub_research_part(scope)}}

    _node.__name__ = f"stub_research_{scope}"
    return _node


def _initial_state(case_id: str) -> dict[str, Any]:
    return {
        "case": CaseState(
            case_id=case_id,
            extracted_facts=ExtractedFacts(
                facts=[
                    {"id": "f-1", "status": "agreed"},
                    {"id": "f-2", "status": "disputed"},
                ]
            ),
            evidence_analysis=EvidenceAnalysis(
                evidence_items=[
                    {"id": "e-1"},
                    {"id": "e-2"},
                ]
            ),
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


async def _drive_original_to_terminal(compiled: Any, case_id: str) -> CaseState:
    """Drive the original through stub pipeline → return the terminal CaseState."""
    config = {"configurable": {"thread_id": case_id}}
    await compiled.ainvoke(_initial_state(case_id), config)
    for _ in range(4):  # gate1 → gate2 → gate3 → gate4
        await compiled.ainvoke(Command(resume={"action": "advance"}), config)
    snap = await compiled.aget_state(config)
    return snap.values["case"]


@pytest.mark.asyncio
async def test_compute_stability_score_runs_n_forks_and_classifies(monkeypatch) -> None:
    """End-to-end fork fan-out: 2 fact_toggles hold, 2 evidence exclusions flip.

    With the divergence-aware synthesis stub, evidence-exclusion forks
    produce ``preliminary_conclusion = "not_liable"`` (different from
    the baseline ``liable``) and fact_toggle forks keep the baseline
    verdict. So 2/4 hold → score 50 → ``highly_sensitive``.
    """
    monkeypatch.setattr(
        "src.pipeline.graph.builder.make_phase_node",
        _make_divergence_aware_phase_factory(),
    )
    monkeypatch.setattr(
        "src.pipeline.graph.builder.make_research_node",
        _stub_research_factory,
    )

    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    case_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"

    baseline_case = await _drive_original_to_terminal(compiled, case_id)
    assert (
        baseline_case.hearing_analysis is not None
        and baseline_case.hearing_analysis.preliminary_conclusion == "liable"
    ), "baseline must carry the unmodified verdict for diffing"

    result = await compute_stability_score(
        graph=compiled,
        case_id=case_id,
        case_state=baseline_case,
        n=4,
        fork_judge_id="stab-test",
    )

    assert result["perturbation_count"] == 4
    assert result["perturbations_held"] == 2, (
        "fact_toggle forks should hold (verdict unchanged); evidence exclusions flip"
    )
    assert result["score"] == 50
    assert result["classification"] == "highly_sensitive"

    held_types = [d["modification_type"] for d in result["details"] if d["verdict_held"]]
    flipped_types = [d["modification_type"] for d in result["details"] if not d["verdict_held"]]
    assert sorted(held_types) == ["fact_toggle", "fact_toggle"]
    assert sorted(flipped_types) == ["evidence_exclusion", "evidence_exclusion"]


@pytest.mark.asyncio
async def test_compute_stability_score_records_failed_perturbations(monkeypatch) -> None:
    """A fork that raises is recorded with ``verdict_held=False`` and an error string.

    Stability should not collapse on a single failed perturbation —
    the caller still wants the score over the surviving forks. We
    simulate failure by making one fork's drive raise.
    """
    monkeypatch.setattr(
        "src.pipeline.graph.builder.make_phase_node",
        _make_divergence_aware_phase_factory(),
    )
    monkeypatch.setattr(
        "src.pipeline.graph.builder.make_research_node",
        _stub_research_factory,
    )

    from src.pipeline.graph.builder import build_graph
    from src.services.whatif import stability as stability_mod

    compiled = build_graph(checkpointer=InMemorySaver())
    case_id = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"

    baseline_case = await _drive_original_to_terminal(compiled, case_id)

    real_drive = stability_mod.drive_whatif_to_terminal
    call_count = {"n": 0}

    async def _flaky_drive(**kwargs: Any) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated fork failure")
        await real_drive(**kwargs)

    monkeypatch.setattr(stability_mod, "drive_whatif_to_terminal", _flaky_drive)

    result = await compute_stability_score(
        graph=compiled,
        case_id=case_id,
        case_state=baseline_case,
        n=4,
        fork_judge_id="stab-test-flaky",
    )

    assert result["perturbation_count"] == 4
    failed = [d for d in result["details"] if "error" in d]
    assert len(failed) == 1
    assert failed[0]["verdict_held"] is False
    assert "simulated fork failure" in failed[0]["error"]
