"""Phase 2 follow-up — integration coverage for gate-2 scoped rerun.

PR #121 unit-tested ``build_resume_payload`` and
``route_to_research_subagents`` in isolation. This integration test
runs them together against a real ``StateGraph`` to prove the
end-to-end invariant the ticket cared about:

> When the judge re-runs a single research subagent, the other
> three subagents' outputs in ``research_parts`` are preserved
> byte-equal — not overwritten.

The test pre-seeds ``research_parts`` with three scopes (mimicking
state captured at the gate-2 pause after a successful first run),
then invokes the graph with ``extra_instructions = {"evidence": ""}``
(the shape ``make_gate_apply`` writes when the judge clicks 'Rerun'
on research-evidence with no corrective notes). Asserts:

1. Only ``research_evidence`` executes — the other three nodes do not.
2. The pre-seeded ``research_parts`` for facts / witnesses / law are
   byte-equal in the post-rerun state.
3. The barrier-fold produces a non-partial ``ResearchOutput`` because
   the dict-keyed reducer merges the fresh evidence run with the
   preserved peer parts.

The four research subagents are stubbed (no LLM call). This is a
topology + reducer integration test, not an LLM e2e test. Real-LLM
runs are covered by Sprint 2 end-to-end suites.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.pipeline.graph.research import (
    RESEARCH_SCOPES,
    RESEARCH_SUBAGENT_NODES,
    research_dispatch_node,
    research_join_node,
    route_to_research_subagents,
)
from src.pipeline.graph.schemas import (
    EvidenceResearch,
    FactsResearch,
    LawResearch,
    PrecedentProvenance,
    ResearchPart,
    WitnessesResearch,
)
from src.pipeline.graph.state import GraphState
from src.shared.case_state import CaseState

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Stubs — match the shape of the real subagents' research_parts updates.
# ---------------------------------------------------------------------------


def _stub_part(scope: str) -> ResearchPart:
    """Build a deterministic ResearchPart for the scope.

    The stub produces equal-content Parts on every call. That's
    enough to test scoped fan-out: the source of truth for whether
    a subagent ran is ``ran_log`` (captured by the stub on each
    invocation), not the Part content.
    """
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
                    retrieved_at=datetime(2026, 4, 26, 0, 0, 0),
                ),
                legal_elements_checklist=[],
                suppressed_citations=[],
            ),
        )
    raise ValueError(f"unknown scope {scope!r}")


def _make_stub_subagent(scope: str, ran_log: list[str]):
    async def _node(_state: dict[str, Any]) -> dict[str, Any]:
        ran_log.append(scope)
        return {"research_parts": {scope: _stub_part(scope)}}

    _node.__name__ = f"stub_research_{scope}"
    return _node


def _build_graph(ran_log: list[str]):
    g = StateGraph(GraphState)
    g.add_node("research_dispatch", research_dispatch_node)
    for scope in RESEARCH_SCOPES:
        g.add_node(RESEARCH_SUBAGENT_NODES[scope], _make_stub_subagent(scope, ran_log))
    g.add_node("research_join", research_join_node)

    g.add_edge(START, "research_dispatch")
    g.add_conditional_edges(
        "research_dispatch",
        route_to_research_subagents,
        list(RESEARCH_SUBAGENT_NODES.values()),
    )
    for scope in RESEARCH_SCOPES:
        g.add_edge(RESEARCH_SUBAGENT_NODES[scope], "research_join")
    g.add_edge("research_join", END)

    return g.compile(checkpointer=InMemorySaver())


def _initial_state(*, extra_instructions: dict[str, str] | None = None,
                   research_parts: dict[str, ResearchPart] | None = None) -> GraphState:
    return GraphState(
        case=CaseState(case_id="00000000-0000-0000-0000-000000000002"),
        run_id="run-2",
        extra_instructions=extra_instructions or {},
        retry_counts={},
        halt=None,
        research_parts=research_parts or {},
        research_output=None,
        is_resume=False,
        start_agent=None,
    )


# ---------------------------------------------------------------------------
# Scoped rerun — only the targeted subagent should execute, peers preserved.
# ---------------------------------------------------------------------------


async def test_scoped_rerun_only_targets_evidence_and_preserves_peers():
    """The end-to-end invariant the ticket cared about.

    Pre-seed ``research_parts`` with the three peer scopes' first-run
    outputs (a faithful proxy for state at the gate-2 pause). Invoke
    with ``extra_instructions = {"evidence": ""}`` — the shape
    ``make_gate_apply`` writes for a no-notes scoped rerun.

    Expectation: only ``research_evidence`` executes; the three peer
    parts are byte-equal in the final state; the join's
    ``ResearchOutput`` is non-partial because the dict-keyed reducer
    merges the fresh evidence run with the preserved peer parts.
    """
    preserved_first_run: dict[str, ResearchPart] = {
        scope: _stub_part(scope) for scope in ("facts", "witnesses", "law")
    }

    ran_log: list[str] = []
    graph = _build_graph(ran_log)
    final = await graph.ainvoke(
        _initial_state(
            extra_instructions={"evidence": ""},
            research_parts={**preserved_first_run},
        ),
        config={"configurable": {"thread_id": "scoped-rerun-evidence"}},
    )

    # 1. Only `evidence` ran. (If the bug were live, all 4 would have run.)
    assert ran_log == ["evidence"], (
        f"Scoped rerun should dispatch only `evidence`; got ran_log={ran_log!r}"
    )

    # 2. Peer parts are byte-equal to their pre-seeded first-run values.
    #    Since the stub did not execute for those scopes, the only way
    #    research_parts[peer] can be present in final state is via the
    #    pre-seeded value carried through the reducer.
    parts = final["research_parts"]
    for scope, original in preserved_first_run.items():
        assert parts[scope] == original, (
            f"Peer scope {scope!r} was overwritten by the rerun; "
            f"expected {original!r}, got {parts[scope]!r}"
        )

    # 3. Evidence is present (rerun produced a fresh stub Part).
    assert parts["evidence"] == _stub_part("evidence")

    # 4. Join produced a non-partial ResearchOutput — reducer merged the
    #    fresh evidence run with the three preserved peer parts.
    out = final["research_output"]
    assert out is not None
    assert out.partial is False
    assert out.evidence is not None
    assert out.facts is not None
    assert out.witnesses is not None
    assert out.law is not None


@pytest.mark.parametrize("target", list(RESEARCH_SCOPES))
async def test_scoped_rerun_for_each_scope_preserves_the_other_three(target: str):
    """Same invariant as the canonical test, parametrised across all four
    scopes so the targeting works symmetrically (no scope is hard-coded
    in the fix)."""
    peers = [s for s in RESEARCH_SCOPES if s != target]
    preserved = {s: _stub_part(s) for s in peers}

    ran_log: list[str] = []
    graph = _build_graph(ran_log)
    final = await graph.ainvoke(
        _initial_state(
            extra_instructions={target: ""},
            research_parts={**preserved},
        ),
        config={"configurable": {"thread_id": f"scoped-rerun-{target}"}},
    )

    assert ran_log == [target]
    for scope in peers:
        assert final["research_parts"][scope] == preserved[scope]
    assert final["research_output"] is not None
    assert final["research_output"].partial is False


# ---------------------------------------------------------------------------
# Legacy 'rerun all four' path — must keep its current behaviour.
# ---------------------------------------------------------------------------


async def test_legacy_full_rerun_with_gate_keyed_extra_instructions_runs_all_four():
    """``extra_instructions = {"gate2": "..."}`` is the legacy 'rerun the
    whole gate' shape ``make_gate_apply`` writes when no subagent is
    targeted. The router must keep falling through to all four scopes.
    Pre-seeded peer parts get overwritten in this path because every
    subagent re-runs."""
    preserved = {scope: _stub_part(scope) for scope in RESEARCH_SCOPES}

    ran_log: list[str] = []
    graph = _build_graph(ran_log)
    final = await graph.ainvoke(
        _initial_state(
            extra_instructions={"gate2": "redo whole research"},
            research_parts={**preserved},
        ),
        config={"configurable": {"thread_id": "legacy-full-rerun"}},
    )

    # Every subagent re-runs — the gate-keyed shape does NOT scope.
    assert sorted(ran_log) == sorted(RESEARCH_SCOPES)
    # All four parts are present; each subagent overwrote its key with
    # a fresh stub Part. Content equals the pre-seeded value (the stub
    # is deterministic) — what we're asserting here is the topology.
    for scope in RESEARCH_SCOPES:
        assert final["research_parts"][scope] == _stub_part(scope)
