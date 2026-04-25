"""Build and compile the VerdictCouncil LangGraph StateGraph.

`build_graph()` is the single entry-point. It assembles the fixed-topology
DAG and returns a compiled graph ready for invocation.

Topology (15 nodes):
    START → pre_run_guardrail → case_processing → complexity_routing
         → gate2_dispatch → [evidence_analysis, fact_reconstruction,
                              witness_analysis, legal_knowledge] (parallel)
         → gate2_join → gate2_retry_router → argument_construction
         → hearing_analysis → hearing_analysis_retry_router
         → hearing_governance → END
Retry-router nodes (gate2_retry_router, hearing_analysis_retry_router) are
Command-returning nodes that atomically increment retry_counts and route.

LangGraph RetryPolicy (max_attempts=2, initial_interval=1s) is applied to
the four L2 agents plus argument_construction, hearing_analysis, and
hearing_governance so transient LLM / network errors auto-recover.
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy

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


def _gate2_retry_router(state: GraphState) -> Command:
    """Node: inspect Gate-2 outputs, increment retry counter, route atomically.

    Replaces the old conditional-edge function so that the retry_counts update
    and the routing decision happen in the same state transition. The partial
    dict update is merged by the _merge_retry_counts reducer in GraphState.
    """
    if state.get("halt"):
        return Command(goto="terminal")

    case = state["case"]
    retry_counts = state.get("retry_counts", {})

    _checks = [
        ("evidence-analysis", "evidence_analysis", case.evidence_analysis is None),
        ("fact-reconstruction", "fact_reconstruction", case.extracted_facts is None),
        ("witness-analysis", "witness_analysis", case.witnesses is None),
        ("legal-knowledge", "legal_knowledge", not case.legal_rules),
    ]

    for agent_key, node_name, failed in _checks:
        if failed and retry_counts.get(agent_key, 0) < _MAX_RETRIES:
            return Command(
                update={"retry_counts": {agent_key: retry_counts.get(agent_key, 0) + 1}},
                goto=node_name,
            )

    return Command(goto="argument_construction")


def _hearing_analysis_retry_router(state: GraphState) -> Command:
    """Node: check hearing-analysis output, increment retry counter, route atomically."""
    if state.get("halt"):
        return Command(goto="terminal")

    case = state["case"]
    ha = case.hearing_analysis
    retry_counts = state.get("retry_counts", {})

    if (
        ha is not None
        and ha.preliminary_conclusion is not None
        and retry_counts.get("hearing-analysis", 0) < _MAX_RETRIES
    ):
        return Command(
            update={
                "retry_counts": {
                    "hearing-analysis": retry_counts.get("hearing-analysis", 0) + 1,
                },
            },
            goto="hearing_analysis",
        )

    return Command(goto="hearing_governance")


def _route_after_hearing_governance(state: GraphState) -> str:
    """Route after hearing-governance: halt on critical fairness issues | END."""
    if state.get("halt"):
        return "terminal"

    case = state["case"]
    fc = case.fairness_check
    if fc is not None and fc.critical_issues_found:
        return "terminal"

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


_FRONTIER_RETRY = RetryPolicy(max_attempts=2, initial_interval=1.0)


def build_graph(checkpointer=None):
    """Build and compile the VerdictCouncil StateGraph.

    Args:
        checkpointer: Optional `BaseCheckpointSaver`. When omitted, the
            module-level singleton from `checkpointer.get_checkpointer()`
            is used (set by FastAPI lifespan / arq startup hooks).
            Tests pass an `InMemorySaver` directly.
    """
    from src.pipeline.graph.checkpointer import get_checkpointer

    if checkpointer is None:
        checkpointer = get_checkpointer()

    graph = StateGraph(GraphState)

    # --- Nodes (15 total) ---
    graph.add_node("pre_run_guardrail", pre_run_guardrail)
    graph.add_node("case_processing", case_processing)
    graph.add_node("complexity_routing", complexity_routing)
    graph.add_node("gate2_dispatch", gate2_dispatch)
    graph.add_node("evidence_analysis", evidence_analysis, retry_policy=_FRONTIER_RETRY)
    graph.add_node("fact_reconstruction", fact_reconstruction, retry_policy=_FRONTIER_RETRY)
    graph.add_node("witness_analysis", witness_analysis, retry_policy=_FRONTIER_RETRY)
    graph.add_node("legal_knowledge", legal_knowledge, retry_policy=_FRONTIER_RETRY)
    graph.add_node("gate2_join", gate2_join)
    graph.add_node("gate2_retry_router", _gate2_retry_router)
    graph.add_node("argument_construction", argument_construction, retry_policy=_FRONTIER_RETRY)
    graph.add_node("hearing_analysis", hearing_analysis, retry_policy=_FRONTIER_RETRY)
    graph.add_node("hearing_analysis_retry_router", _hearing_analysis_retry_router)
    graph.add_node("hearing_governance", hearing_governance, retry_policy=_FRONTIER_RETRY)
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

    # --- Gate 2 post-barrier routing (retry-router node handles counter + routing) ---
    graph.add_edge("gate2_join", "gate2_retry_router")

    # --- Gate 3 ---
    graph.add_edge("argument_construction", "hearing_analysis")
    graph.add_edge("hearing_analysis", "hearing_analysis_retry_router")

    # --- Gate 4 ---
    graph.add_conditional_edges(
        "hearing_governance",
        _route_after_hearing_governance,
        {"terminal": "terminal", END: END},
    )

    # Terminal is a sink
    graph.add_edge("terminal", END)

    return graph.compile(checkpointer=checkpointer)
