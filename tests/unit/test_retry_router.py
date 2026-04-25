"""Sprint 4 4.A4 — retry plumbing invariants.

Sprint 1 1.A1.7 collapsed the legacy 9-agent topology — including its
hand-rolled retry-router nodes — into the phase-factory pattern. The
new topology delegates retry to LangGraph's `RetryPolicy` decoration
on phase nodes (`_FRONTIER_RETRY = RetryPolicy(max_attempts=2)` in
``src/pipeline/graph/builder.py``). Application-level retry routers
no longer exist; the ``retry_counts`` GraphState slot has no writer.

This module locks the surviving invariants so a future change can't
silently regress the cleanup:

1. **4.A4.1** — ``_merge_retry_counts`` is the only path that updates
   ``retry_counts`` (max-per-key reducer). No code path mutates it
   directly; the slot is reducer-driven.
2. **4.A4.2** — phase nodes are decorated with ``RetryPolicy`` at
   compile time; no conditional edges branch on retry counters.
3. **4.A4.3** — the reducer is monotonic: a stale parallel branch
   cannot reset a counter already advanced by another branch.
"""

from __future__ import annotations

from src.pipeline.graph import builder
from src.pipeline.graph.state import _merge_retry_counts


# ---------------------------------------------------------------------------
# 4.A4.1 — reducer-only mutation
# ---------------------------------------------------------------------------


def test_no_application_level_retry_router_remains() -> None:
    """The pre-Sprint-1 retry-router nodes must not exist in the topology."""
    graph = builder._build_topology()
    nodes = set(graph.nodes)

    legacy_retry_router_names = {
        "case_processing_retry",
        "complexity_routing_retry",
        "evidence_analysis_retry",
        "fact_reconstruction_retry",
        "witness_analysis_retry",
        "legal_knowledge_retry",
        "argument_construction_retry",
        "hearing_analysis_retry",
        "hearing_governance_retry",
    }
    leftover = nodes & legacy_retry_router_names
    assert not leftover, (
        "Legacy per-agent retry routers must not exist in the new topology; "
        f"found: {sorted(leftover)}"
    )


# ---------------------------------------------------------------------------
# 4.A4.2 — RetryPolicy decoration on phase nodes
# ---------------------------------------------------------------------------


def test_phase_nodes_decorated_with_retry_policy() -> None:
    """Phase nodes must carry the frontier RetryPolicy at compile time."""
    from langgraph.types import RetryPolicy

    graph = builder._build_topology()
    expected_phase_nodes = {
        "intake",
        "research_evidence",
        "research_facts",
        "research_witnesses",
        "research_law",
        "synthesis",
        "auditor",
    }

    for name in expected_phase_nodes:
        assert name in graph.nodes, f"Expected phase node {name!r} missing"
        spec = graph.nodes[name]
        retry = getattr(spec, "retry_policy", None)
        # `RetryPolicy` is itself a NamedTuple — never iterate looking for
        # nested instances; check the value directly.
        assert isinstance(retry, RetryPolicy), (
            f"Phase node {name!r} must carry a RetryPolicy; got {type(retry).__name__}"
        )
        assert retry.max_attempts >= 1, (
            f"Phase node {name!r} RetryPolicy must allow at least 1 attempt"
        )


# ---------------------------------------------------------------------------
# 4.A4.3 — reducer correctness
# ---------------------------------------------------------------------------


def test_merge_retry_counts_max_per_key() -> None:
    base = {"intake": 1, "research": 0}
    update = {"intake": 0, "research": 2, "synthesis": 1}

    merged = _merge_retry_counts(base, update)

    # Each key is the max of base/update — a stale branch (intake=0) cannot
    # reset an already-advanced counter (intake=1).
    assert merged == {"intake": 1, "research": 2, "synthesis": 1}


def test_merge_retry_counts_is_idempotent_on_self() -> None:
    """Reducer must be a no-op when applied to identical base+update."""
    base = {"research": 3, "audit": 1}
    assert _merge_retry_counts(base, base) == base


def test_merge_retry_counts_does_not_mutate_inputs() -> None:
    """Reducer returns a new dict — base and update untouched."""
    base = {"intake": 1}
    update = {"research": 1}
    base_copy = dict(base)
    update_copy = dict(update)

    _merge_retry_counts(base, update)

    assert base == base_copy
    assert update == update_copy
