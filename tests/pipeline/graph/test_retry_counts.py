"""P0.1 acceptance tests — retry_counts reducer and cap enforcement."""

from __future__ import annotations

import pytest

from src.pipeline.graph.builder import _MAX_RETRIES, _gate2_retry_router, _hearing_analysis_retry_router
from src.pipeline.graph.state import GraphState, _merge_retry_counts
from src.shared.case_state import (
    CaseState,
    EvidenceAnalysis,
    ExtractedFacts,
    HearingAnalysis,
    Witnesses,
)


def _state(case: CaseState | None = None, **kwargs) -> dict:
    return {
        "case": case or CaseState(),
        "run_id": "run-test",
        "extra_instructions": {},
        "retry_counts": {},
        "halt": None,
        "mlflow_run_ids": {},
        "is_resume": False,
        "start_agent": None,
        **kwargs,
    }


_FULL_CASE = CaseState(
    evidence_analysis=EvidenceAnalysis(evidence_items=[{"id": "e1"}]),
    extracted_facts=ExtractedFacts(facts=[{"fact": "A"}]),
    witnesses=Witnesses(witnesses=[{"name": "Alice"}]),
    legal_rules=[{"rule": "R1"}],
)


# ---------------------------------------------------------------------------
# (a) First failed owned-field write triggers exactly one retry
# ---------------------------------------------------------------------------


class TestSingleRetryFires:
    def test_first_call_retries_missing_evidence(self):
        """Counter starts at 0; first router call must retry and bump to 1."""
        case = _FULL_CASE.model_copy(update={"evidence_analysis": None})
        cmd = _gate2_retry_router(_state(case=case, retry_counts={}))

        assert cmd.goto == "evidence_analysis"
        assert cmd.update == {"retry_counts": {"evidence-analysis": 1}}

    def test_second_call_at_max_retries_advances(self):
        """Counter already at _MAX_RETRIES (1); second call must advance, not loop."""
        case = _FULL_CASE.model_copy(update={"evidence_analysis": None})
        cmd = _gate2_retry_router(
            _state(case=case, retry_counts={"evidence-analysis": _MAX_RETRIES})
        )

        assert cmd.goto == "argument_construction"
        assert not cmd.update

    def test_hearing_analysis_retry_fires_once(self):
        case = CaseState(hearing_analysis=HearingAnalysis(preliminary_conclusion="guilty"))
        cmd = _hearing_analysis_retry_router(_state(case=case, retry_counts={}))

        assert cmd.goto == "hearing_analysis"
        assert cmd.update == {"retry_counts": {"hearing-analysis": 1}}

    def test_hearing_analysis_at_max_retries_advances(self):
        case = CaseState(hearing_analysis=HearingAnalysis(preliminary_conclusion="guilty"))
        cmd = _hearing_analysis_retry_router(
            _state(case=case, retry_counts={"hearing-analysis": _MAX_RETRIES})
        )

        assert cmd.goto == "hearing_governance"
        assert not cmd.update


# ---------------------------------------------------------------------------
# (b) Second failure routes per policy — does not loop indefinitely
# ---------------------------------------------------------------------------


class TestCapEnforcement:
    @pytest.mark.parametrize(
        "agent_key,node_name,case_patch",
        [
            ("evidence-analysis", "evidence_analysis", {"evidence_analysis": None}),
            ("fact-reconstruction", "fact_reconstruction", {"extracted_facts": None}),
            ("witness-analysis", "witness_analysis", {"witnesses": None}),
            ("legal-knowledge", "legal_knowledge", {"legal_rules": []}),
        ],
    )
    def test_each_l2_agent_capped_at_max_retries(self, agent_key, node_name, case_patch):
        """All four L2 agents stop retrying at _MAX_RETRIES and advance."""
        case = _FULL_CASE.model_copy(update=case_patch)
        cmd_first = _gate2_retry_router(_state(case=case, retry_counts={}))
        assert cmd_first.goto == node_name, "first call should retry"

        cmd_second = _gate2_retry_router(
            _state(case=case, retry_counts={agent_key: _MAX_RETRIES})
        )
        assert cmd_second.goto == "argument_construction", "second call should advance"
        assert not cmd_second.update


# ---------------------------------------------------------------------------
# (c) Parallel branch retry deltas merge correctly — reducer test
# ---------------------------------------------------------------------------


class TestRetryCountsReducer:
    def test_empty_base_takes_update(self):
        result = _merge_retry_counts({}, {"evidence-analysis": 1})
        assert result == {"evidence-analysis": 1}

    def test_existing_key_takes_max(self):
        """Stale branch with count=0 must not reset a counter already at 1."""
        result = _merge_retry_counts({"evidence-analysis": 1}, {"evidence-analysis": 0})
        assert result["evidence-analysis"] == 1

    def test_new_key_from_parallel_branch_is_added(self):
        result = _merge_retry_counts(
            {"evidence-analysis": 1},
            {"fact-reconstruction": 1},
        )
        assert result == {"evidence-analysis": 1, "fact-reconstruction": 1}

    def test_two_parallel_increments_both_persist(self):
        """Simulate two L2 router calls returning partial dicts that are then merged."""
        base: dict[str, int] = {}
        after_first = _merge_retry_counts(base, {"evidence-analysis": 1})
        after_second = _merge_retry_counts(after_first, {"fact-reconstruction": 1})
        assert after_second == {"evidence-analysis": 1, "fact-reconstruction": 1}

    def test_higher_count_wins_over_lower(self):
        """If somehow two paths deliver different counts, the higher wins."""
        result = _merge_retry_counts({"evidence-analysis": 1}, {"evidence-analysis": 2})
        assert result["evidence-analysis"] == 2
