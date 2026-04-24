"""Build and compile the VerdictCouncil LangGraph StateGraph.

`build_graph(checkpointer)` is the single entry-point. It assembles the
fixed-topology DAG and returns a compiled graph ready for invocation.

Topology (matches plan):
    START → pre_run_guardrail → case_processing → complexity_routing
         → gate2_dispatch → [evidence_analysis, fact_reconstruction,
                              witness_analysis, legal_knowledge] (parallel)
         → gate2_join → argument_construction → hearing_analysis
         → hearing_governance → END
Conditional routing handles escalation, gate pauses, and retry at each gate.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from src.pipeline.graph.nodes.argument_construction import argument_construction
from src.pipeline.graph.nodes.case_processing import case_processing
from src.pipeline.graph.nodes.complexity_routing import complexity_routing
from src.pipeline.graph.nodes.evidence_analysis import evidence_analysis
from src.pipeline.graph.nodes.fact_reconstruction import fact_reconstruction
from src.pipeline.graph.nodes.gate2_dispatch import gate2_dispatch
from src.pipeline.graph.nodes.gate2_join import gate2_join
from src.pipeline.graph.nodes.hearing_analysis import hearing_analysis
from src.pipeline.graph.nodes.hearing_governance import hearing_governance
from src.pipeline.graph.nodes.hooks import pre_run_guardrail
from src.pipeline.graph.nodes.legal_knowledge import legal_knowledge
from src.pipeline.graph.nodes.terminal import terminal
from src.pipeline.graph.nodes.witness_analysis import witness_analysis
from src.pipeline.graph.state import GraphState
from src.shared.case_state import CaseStatusEnum

logger = logging.getLogger(__name__)

_MAX_RETRIES = 1


# ---------------------------------------------------------------------------
# Routing / conditional edge functions
# ---------------------------------------------------------------------------


def _route_after_complexity_routing(state: GraphState) -> str:
    """Route after complexity-routing: halt | gate-pause | advance."""
    if state.get("halt"):
        return "terminal"
    status = state["case"].status
    if status in (CaseStatusEnum.awaiting_review_gate1, CaseStatusEnum.escalated):
        return END
    return "gate2_dispatch"


def _route_after_gate2_join(state: GraphState) -> str:
    """Route after gate2_join: halt | retry a failed L2 agent | advance."""
    if state.get("halt"):
        return "terminal"

    case = state["case"]
    retry_counts = state.get("retry_counts", {})

    # Check if any Gate-2 agent is below retry threshold and has empty outputs
    if case.evidence_analysis is None and retry_counts.get("evidence-analysis", 0) < _MAX_RETRIES:
        return "evidence_analysis"
    if case.extracted_facts is None and retry_counts.get("fact-reconstruction", 0) < _MAX_RETRIES:
        return "fact_reconstruction"
    if case.witnesses is None and retry_counts.get("witness-analysis", 0) < _MAX_RETRIES:
        return "witness_analysis"
    if not case.legal_rules and retry_counts.get("legal-knowledge", 0) < _MAX_RETRIES:
        return "legal_knowledge"

    # All agents complete (or max retries reached) — advance
    return "argument_construction"


def _route_after_hearing_analysis(state: GraphState) -> str:
    """Route after hearing-analysis: halt | retry (preliminary_conclusion set) | advance."""
    if state.get("halt"):
        return "terminal"

    case = state["case"]
    ha = case.hearing_analysis
    retry_counts = state.get("retry_counts", {})

    # Retry condition: preliminary_conclusion must remain null until hearing_governance sets it
    if (
        ha is not None
        and ha.preliminary_conclusion is not None
        and retry_counts.get("hearing-analysis", 0) < _MAX_RETRIES
    ):
        return "hearing_analysis"

    return "hearing_governance"


def _route_after_hearing_governance(state: GraphState) -> str:
    """Route after hearing-governance: halt on critical fairness issues | END."""
    if state.get("halt"):
        return "terminal"

    case = state["case"]
    fc = case.fairness_check
    if fc is not None and fc.critical_issues_found:
        return "terminal"

    status = case.status
    if status in (CaseStatusEnum.awaiting_review_gate4, CaseStatusEnum.escalated):
        return END
    return END


def _route_after_case_processing(state: GraphState) -> str:
    """Route after case-processing: halt on failure | advance."""
    if state.get("halt"):
        return "terminal"
    case = state["case"]
    if case.status in (CaseStatusEnum.failed, CaseStatusEnum.escalated):
        return "terminal"
    return "complexity_routing"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def build_graph(checkpointer: Any = None):
    """Build and compile the VerdictCouncil StateGraph.

    Args:
        checkpointer: Optional AsyncPostgresSaver for graph-level replay.

    Returns:
        Compiled LangGraph application.
    """
    graph = StateGraph(GraphState)

    # --- Nodes ---
    graph.add_node("pre_run_guardrail", pre_run_guardrail)
    graph.add_node("case_processing", case_processing)
    graph.add_node("complexity_routing", complexity_routing)
    graph.add_node("gate2_dispatch", gate2_dispatch)
    graph.add_node("evidence_analysis", evidence_analysis)
    graph.add_node("fact_reconstruction", fact_reconstruction)
    graph.add_node("witness_analysis", witness_analysis)
    graph.add_node("legal_knowledge", legal_knowledge)
    graph.add_node("gate2_join", gate2_join)
    graph.add_node("argument_construction", argument_construction)
    graph.add_node("hearing_analysis", hearing_analysis)
    graph.add_node("hearing_governance", hearing_governance)
    graph.add_node("terminal", terminal)

    # --- Entry ---
    # When start_agent is set (gate-by-gate / what-if), skip to the named node.
    _jump_targets = {
        "case_processing": "case_processing",
        "gate2_dispatch": "gate2_dispatch",
        "argument_construction": "argument_construction",
        "hearing_governance": "hearing_governance",
        # Allow individual L2 agent retries as entry
        "evidence_analysis": "evidence_analysis",
        "fact_reconstruction": "fact_reconstruction",
        "witness_analysis": "witness_analysis",
        "legal_knowledge": "legal_knowledge",
    }

    def _route_after_pre_run_guardrail(state: GraphState) -> str:
        start = state.get("start_agent")
        if start and start in _jump_targets:
            return start
        return "case_processing"

    graph.add_edge(START, "pre_run_guardrail")
    graph.add_conditional_edges(
        "pre_run_guardrail",
        _route_after_pre_run_guardrail,
        {**_jump_targets, "case_processing": "case_processing"},
    )

    # --- Gate 1 ---
    graph.add_conditional_edges(
        "case_processing",
        _route_after_case_processing,
        {"complexity_routing": "complexity_routing", "terminal": "terminal"},
    )
    graph.add_conditional_edges(
        "complexity_routing",
        _route_after_complexity_routing,
        {
            "gate2_dispatch": "gate2_dispatch",
            "terminal": "terminal",
            END: END,
        },
    )

    # --- Gate 2 fan-out (static parallel edges) ---
    graph.add_edge("gate2_dispatch", "evidence_analysis")
    graph.add_edge("gate2_dispatch", "fact_reconstruction")
    graph.add_edge("gate2_dispatch", "witness_analysis")
    graph.add_edge("gate2_dispatch", "legal_knowledge")

    # --- Gate 2 implicit barrier join ---
    graph.add_edge("evidence_analysis", "gate2_join")
    graph.add_edge("fact_reconstruction", "gate2_join")
    graph.add_edge("witness_analysis", "gate2_join")
    graph.add_edge("legal_knowledge", "gate2_join")

    # --- Gate 2 post-barrier routing (retry | advance) ---
    graph.add_conditional_edges(
        "gate2_join",
        _route_after_gate2_join,
        {
            "evidence_analysis": "evidence_analysis",
            "fact_reconstruction": "fact_reconstruction",
            "witness_analysis": "witness_analysis",
            "legal_knowledge": "legal_knowledge",
            "argument_construction": "argument_construction",
            "terminal": "terminal",
        },
    )

    # --- Gate 3 ---
    graph.add_edge("argument_construction", "hearing_analysis")
    graph.add_conditional_edges(
        "hearing_analysis",
        _route_after_hearing_analysis,
        {
            "hearing_analysis": "hearing_analysis",
            "hearing_governance": "hearing_governance",
            "terminal": "terminal",
        },
    )

    # --- Gate 4 ---
    graph.add_conditional_edges(
        "hearing_governance",
        _route_after_hearing_governance,
        {"terminal": "terminal", END: END},
    )

    # Terminal is a sink
    graph.add_edge("terminal", END)

    return graph.compile(checkpointer=checkpointer)
