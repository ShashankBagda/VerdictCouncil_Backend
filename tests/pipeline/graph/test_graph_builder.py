"""Tests for src.pipeline.graph.builder — topology verification."""

from __future__ import annotations

from src.pipeline.graph.builder import (
    _gate2_retry_router,
    _hearing_analysis_retry_router,
    _route_after_case_processing,
    _route_after_complexity_routing,
    _route_after_hearing_governance,
    build_graph,
)
from src.shared.case_state import (
    CaseState,
    CaseStatusEnum,
    EvidenceAnalysis,
    ExtractedFacts,
    FairnessCheck,
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


# ---------------------------------------------------------------------------
# Graph compilation
# ---------------------------------------------------------------------------


class TestBuildGraph:
    def test_graph_compiles_without_checkpointer(self):
        g = build_graph()
        assert g is not None

    def test_graph_has_all_15_nodes(self):
        g = build_graph()
        nodes = set(g.get_graph().nodes.keys())
        expected = {
            "__start__",
            "pre_run_guardrail",
            "case_processing",
            "complexity_routing",
            "gate2_dispatch",
            "evidence_analysis",
            "fact_reconstruction",
            "witness_analysis",
            "legal_knowledge",
            "gate2_join",
            "gate2_retry_router",
            "argument_construction",
            "hearing_analysis",
            "hearing_analysis_retry_router",
            "hearing_governance",
            "terminal",
            "__end__",
        }
        assert expected.issubset(nodes)

    def test_gate2_parallel_edges_from_dispatch(self):
        """gate2_dispatch must have exactly 4 outgoing static edges."""
        g = build_graph()
        edges = g.get_graph().edges
        dispatch_targets = {e[1] for e in edges if e[0] == "gate2_dispatch"}
        assert dispatch_targets == {
            "evidence_analysis",
            "fact_reconstruction",
            "witness_analysis",
            "legal_knowledge",
        }

    def test_gate2_all_agents_join_to_gate2_join(self):
        """All 4 L2 agents must have an outgoing edge to gate2_join."""
        g = build_graph()
        edges = g.get_graph().edges
        l2_to_join = {e[0] for e in edges if e[1] == "gate2_join"}
        assert l2_to_join == {
            "evidence_analysis",
            "fact_reconstruction",
            "witness_analysis",
            "legal_knowledge",
        }

    def test_terminal_is_sink(self):
        """terminal node must have exactly one edge: to __end__."""
        g = build_graph()
        edges = g.get_graph().edges
        from_terminal = [e for e in edges if e[0] == "terminal"]
        assert len(from_terminal) == 1
        assert from_terminal[0][1] == "__end__"


# ---------------------------------------------------------------------------
# _route_after_case_processing
# ---------------------------------------------------------------------------


class TestRouteAfterCaseProcessing:
    def test_halt_routes_to_terminal(self):
        state = _state(halt={"reason": "test"})
        assert _route_after_case_processing(state) == "terminal"

    def test_failed_status_routes_to_terminal(self):
        state = _state(case=CaseState(status=CaseStatusEnum.failed))
        assert _route_after_case_processing(state) == "terminal"

    def test_normal_routes_to_complexity_routing(self):
        state = _state(case=CaseState(status=CaseStatusEnum.processing))
        assert _route_after_case_processing(state) == "complexity_routing"


# ---------------------------------------------------------------------------
# _route_after_complexity_routing
# ---------------------------------------------------------------------------


class TestRouteAfterComplexityRouting:
    def test_halt_routes_to_terminal(self):
        state = _state(halt={"reason": "test"})
        assert _route_after_complexity_routing(state) == "terminal"

    def test_awaiting_gate1_review_routes_to_end(self):
        from langgraph.graph import END

        state = _state(case=CaseState(status=CaseStatusEnum.awaiting_review_gate1))
        assert _route_after_complexity_routing(state) == END

    def test_escalated_routes_to_end(self):
        from langgraph.graph import END

        state = _state(case=CaseState(status=CaseStatusEnum.escalated))
        assert _route_after_complexity_routing(state) == END

    def test_processing_routes_to_gate2_dispatch(self):
        state = _state(case=CaseState(status=CaseStatusEnum.processing))
        assert _route_after_complexity_routing(state) == "gate2_dispatch"


# ---------------------------------------------------------------------------
# _gate2_retry_router
# ---------------------------------------------------------------------------


class TestGate2RetryRouter:
    def test_halt_routes_to_terminal(self):
        cmd = _gate2_retry_router(_state(halt={"reason": "barrier_timeout"}))
        assert cmd.goto == "terminal"
        assert not cmd.update

    def test_all_complete_advances_to_argument_construction(self):
        case = CaseState(
            evidence_analysis=EvidenceAnalysis(evidence_items=[{"id": "e1"}]),
            extracted_facts=ExtractedFacts(facts=[{"fact": "A"}]),
            witnesses=Witnesses(witnesses=[{"name": "Alice"}]),
            legal_rules=[{"rule": "R1"}],
        )
        cmd = _gate2_retry_router(_state(case=case))
        assert cmd.goto == "argument_construction"
        assert not cmd.update

    def test_missing_evidence_triggers_retry_and_increments_counter(self):
        case = CaseState(
            evidence_analysis=None,
            extracted_facts=ExtractedFacts(facts=[{"fact": "A"}]),
            witnesses=Witnesses(witnesses=[{"name": "Alice"}]),
            legal_rules=[{"rule": "R1"}],
        )
        cmd = _gate2_retry_router(_state(case=case, retry_counts={"evidence-analysis": 0}))
        assert cmd.goto == "evidence_analysis"
        assert cmd.update == {"retry_counts": {"evidence-analysis": 1}}

    def test_missing_evidence_at_max_retries_advances(self):
        case = CaseState(
            evidence_analysis=None,
            extracted_facts=ExtractedFacts(facts=[{"fact": "A"}]),
            witnesses=Witnesses(witnesses=[{"name": "Alice"}]),
            legal_rules=[{"rule": "R1"}],
        )
        cmd = _gate2_retry_router(_state(case=case, retry_counts={"evidence-analysis": 1}))
        assert cmd.goto == "argument_construction"
        assert not cmd.update

    def test_missing_legal_rules_triggers_retry_and_increments_counter(self):
        case = CaseState(
            evidence_analysis=EvidenceAnalysis(evidence_items=[{"id": "e1"}]),
            extracted_facts=ExtractedFacts(facts=[{"fact": "A"}]),
            witnesses=Witnesses(witnesses=[{"name": "Alice"}]),
            legal_rules=[],
        )
        cmd = _gate2_retry_router(_state(case=case, retry_counts={}))
        assert cmd.goto == "legal_knowledge"
        assert cmd.update == {"retry_counts": {"legal-knowledge": 1}}

    def test_first_failed_agent_wins_retry_priority(self):
        """evidence-analysis is checked first; both being empty routes to evidence_analysis."""
        case = CaseState(
            evidence_analysis=None,
            extracted_facts=None,
            witnesses=Witnesses(witnesses=[{"name": "Alice"}]),
            legal_rules=[{"rule": "R1"}],
        )
        cmd = _gate2_retry_router(_state(case=case, retry_counts={}))
        assert cmd.goto == "evidence_analysis"
        assert cmd.update == {"retry_counts": {"evidence-analysis": 1}}


# ---------------------------------------------------------------------------
# _hearing_analysis_retry_router
# ---------------------------------------------------------------------------


class TestHearingAnalysisRetryRouter:
    def test_halt_routes_to_terminal(self):
        cmd = _hearing_analysis_retry_router(_state(halt={"reason": "test"}))
        assert cmd.goto == "terminal"
        assert not cmd.update

    def test_null_preliminary_conclusion_advances(self):
        case = CaseState(hearing_analysis=HearingAnalysis(preliminary_conclusion=None))
        cmd = _hearing_analysis_retry_router(_state(case=case))
        assert cmd.goto == "hearing_governance"
        assert not cmd.update

    def test_non_null_preliminary_conclusion_triggers_retry_and_increments(self):
        case = CaseState(hearing_analysis=HearingAnalysis(preliminary_conclusion="guilty"))
        cmd = _hearing_analysis_retry_router(_state(case=case, retry_counts={"hearing-analysis": 0}))
        assert cmd.goto == "hearing_analysis"
        assert cmd.update == {"retry_counts": {"hearing-analysis": 1}}

    def test_non_null_at_max_retries_advances(self):
        case = CaseState(hearing_analysis=HearingAnalysis(preliminary_conclusion="guilty"))
        cmd = _hearing_analysis_retry_router(_state(case=case, retry_counts={"hearing-analysis": 1}))
        assert cmd.goto == "hearing_governance"
        assert not cmd.update

    def test_no_hearing_analysis_advances(self):
        cmd = _hearing_analysis_retry_router(_state(case=CaseState(hearing_analysis=None)))
        assert cmd.goto == "hearing_governance"
        assert not cmd.update


# ---------------------------------------------------------------------------
# _route_after_hearing_governance
# ---------------------------------------------------------------------------


class TestRouteAfterHearingGovernance:
    def test_halt_routes_to_terminal(self):
        state = _state(halt={"reason": "test"})
        assert _route_after_hearing_governance(state) == "terminal"

    def test_critical_fairness_issues_route_to_terminal(self):
        fc = FairnessCheck(
            critical_issues_found=True,
            audit_passed=False,
            issues=["bias detected"],
            recommendations=[],
        )
        case = CaseState(fairness_check=fc)
        state = _state(case=case)
        assert _route_after_hearing_governance(state) == "terminal"

    def test_clean_fairness_check_routes_to_end(self):
        from langgraph.graph import END

        fc = FairnessCheck(
            critical_issues_found=False,
            audit_passed=True,
            issues=[],
            recommendations=[],
        )
        case = CaseState(fairness_check=fc, status=CaseStatusEnum.processing)
        state = _state(case=case)
        assert _route_after_hearing_governance(state) == END

    def test_no_fairness_check_routes_to_end(self):
        from langgraph.graph import END

        state = _state(case=CaseState(fairness_check=None))
        assert _route_after_hearing_governance(state) == END
