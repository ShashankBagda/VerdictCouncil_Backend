"""Sprint 1 1.A1.7 — graph topology integration test.

Asserts the new 6-phase StateGraph topology compiles and exposes the
expected node set, and that the legacy 9-agent topology is gone.

The new topology in compact form: see `src/pipeline/graph/builder.py`
docstring for the full diagram. Compile-time invariants only — node
bodies are not exercised here.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from src.pipeline.graph.builder import build_graph

EXPECTED_NEW_NODES: frozenset[str] = frozenset(
    {
        "intake",
        "gate1_pause",
        "gate1_apply",
        "research_dispatch",
        "research_evidence",
        "research_facts",
        "research_witnesses",
        "research_law",
        "research_join",
        "gate2_pause",
        "gate2_apply",
        "synthesis",
        "gate3_pause",
        "gate3_apply",
        "auditor",
        "gate4_pause",
        "gate4_apply",
        "terminal",
    }
)

LEGACY_NODES_THAT_MUST_BE_GONE: frozenset[str] = frozenset(
    {
        "pre_run_guardrail",
        "case_processing",
        "complexity_routing",
        "gate2_dispatch",
        "gate2_join",
        "gate2_retry_router",
        "evidence_analysis",
        "fact_reconstruction",
        "witness_analysis",
        "legal_knowledge",
        "argument_construction",
        "hearing_analysis",
        "hearing_analysis_retry_router",
        "hearing_governance",
    }
)


def _node_names(compiled) -> set[str]:
    """Return the user-declared node names on the compiled graph.

    LangGraph injects internal `__start__` / `__end__` sentinels into the
    builder's node map; strip those so the assertions read cleanly.
    """
    raw = set(compiled.builder.nodes.keys())
    return {n for n in raw if not n.startswith("__")}


def test_compiled_graph_contains_all_expected_phase_and_gate_nodes() -> None:
    compiled = build_graph(checkpointer=InMemorySaver())
    nodes = _node_names(compiled)

    missing = EXPECTED_NEW_NODES - nodes
    assert not missing, f"New topology is missing required nodes: {sorted(missing)}"


def test_compiled_graph_does_not_retain_legacy_9_agent_topology() -> None:
    compiled = build_graph(checkpointer=InMemorySaver())
    nodes = _node_names(compiled)

    leftover = LEGACY_NODES_THAT_MUST_BE_GONE & nodes
    assert not leftover, (
        "Legacy 9-agent nodes must be removed by 1.A1.7 + 1.A1.6: "
        f"still present: {sorted(leftover)}"
    )


def test_research_dispatch_uses_conditional_edge_send_factory() -> None:
    """V-4 contract: Send fan-out goes through `add_conditional_edges`.

    A node returning `list[Send]` from its body is the wrong pattern. The
    conditional-edge router is registered on the `branches` map keyed by
    source node name; the absence of that entry is the signal that the
    builder fell back to a node-returns-Send shape.
    """
    compiled = build_graph(checkpointer=InMemorySaver())
    branches = compiled.builder.branches
    assert "research_dispatch" in branches, (
        "research_dispatch must use add_conditional_edges with a Send-returning "
        "router (V-4); no conditional edges registered for that source node."
    )
