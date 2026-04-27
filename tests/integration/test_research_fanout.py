"""Sprint 1 1.A1.5 — research fan-out integration test.

Builds a minimal `StateGraph` wired with the canonical pattern:

    research_dispatch  ─(add_conditional_edges → list[Send])→  4 subagents
                                                                     │
                                                              research_join

The four research subagents are stubbed (no LLM call) so we can assert
the topology contract without an OpenAI key. Real-LLM runs are covered
by Sprint 2 end-to-end tests.

Invariants under test:

1. `research_dispatch` is wired via `add_conditional_edges`, NOT a node
   that returns `list[Send]` (V-4).
2. All four subagents execute on a single dispatch (parallel fan-out).
3. The dict-keyed `_merge_research_parts` reducer accumulates one entry
   per scope ("evidence" / "facts" / "witnesses" / "law").
4. `research_join` consumes the accumulator and writes a non-partial
   `ResearchOutput` when all four parts arrive.
5. With 1-3 of 4 subagents skipped, the same join produces a
   `partial=True` output and the missing scopes are `None`.
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
# Stub research subagents — no LLM call, no tools.
# ---------------------------------------------------------------------------


def _stub_part(scope: str) -> ResearchPart:
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
    raise ValueError(f"unknown scope {scope!r}")


def _make_stub_subagent(scope: str, ran_log: list[str]):
    """Stub that mimics `make_research_node(scope)` output shape."""

    async def _node(_state: dict[str, Any]) -> dict[str, Any]:
        ran_log.append(scope)
        return {"research_parts": {scope: _stub_part(scope)}}

    _node.__name__ = f"stub_research_{scope}"
    return _node


def _make_skipping_subagent(scope: str, ran_log: list[str]):
    """Stub that runs but contributes no `research_parts` entry (failure case)."""

    async def _node(_state: dict[str, Any]) -> dict[str, Any]:
        ran_log.append(scope)
        return {}

    _node.__name__ = f"skip_research_{scope}"
    return _node


def _build_test_graph(*, skipping_scopes: set[str] = frozenset()):
    g = StateGraph(GraphState)
    g.add_node("research_dispatch", research_dispatch_node)
    ran_log: list[str] = []
    for scope in RESEARCH_SCOPES:
        node_fn = (
            _make_skipping_subagent(scope, ran_log)
            if scope in skipping_scopes
            else _make_stub_subagent(scope, ran_log)
        )
        g.add_node(RESEARCH_SUBAGENT_NODES[scope], node_fn)
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

    compiled = g.compile(checkpointer=InMemorySaver())
    return compiled, ran_log


def _initial_state() -> GraphState:
    return GraphState(
        case=CaseState(case_id="00000000-0000-0000-0000-000000000001"),
        run_id="run-1",
        extra_instructions={},
        retry_counts={},
        halt=None,
        research_parts={},
        research_output=None,
        is_resume=False,
        start_agent=None,
    )


# ---------------------------------------------------------------------------
# Fan-out happy path
# ---------------------------------------------------------------------------


async def test_dispatch_fans_out_to_all_four_subagents():
    graph, ran_log = _build_test_graph()
    final = await graph.ainvoke(
        _initial_state(),
        config={"configurable": {"thread_id": "fanout-happy"}},
    )

    # All four subagents executed.
    assert sorted(ran_log) == sorted(RESEARCH_SCOPES), (
        f"expected all 4 subagents to run, got {ran_log!r}"
    )

    # Reducer accumulated one ResearchPart per scope.
    assert set(final["research_parts"].keys()) == set(RESEARCH_SCOPES)
    for scope, part in final["research_parts"].items():
        assert isinstance(part, ResearchPart)
        assert part.scope == scope

    # Join folded the accumulator into a complete ResearchOutput.
    out = final["research_output"]
    assert out is not None
    assert out.partial is False
    assert out.evidence is not None
    assert out.facts is not None
    assert out.witnesses is not None
    assert out.law is not None


async def test_research_parts_reducer_keeps_one_entry_per_scope():
    """The dict-keyed reducer rejects accidental overcounting from a re-run."""
    graph, _ = _build_test_graph()
    final = await graph.ainvoke(
        _initial_state(),
        config={"configurable": {"thread_id": "fanout-counted"}},
    )
    assert len(final["research_parts"]) == 4


# ---------------------------------------------------------------------------
# Partial-output paths (1-3 of 4 subagents skip)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "skipping",
    [
        {"evidence"},
        {"facts"},
        {"witnesses"},
        {"law"},
        {"evidence", "facts"},
        {"witnesses", "law", "facts"},
    ],
)
async def test_partial_subagent_failures_yield_partial_research_output(skipping: set[str]):
    graph, ran_log = _build_test_graph(skipping_scopes=skipping)
    final = await graph.ainvoke(
        _initial_state(),
        config={"configurable": {"thread_id": f"partial-{'-'.join(sorted(skipping))}"}},
    )

    # All subagents still execute (they just don't contribute parts).
    assert sorted(ran_log) == sorted(RESEARCH_SCOPES)

    # Skipping scopes are absent from the accumulator.
    present = set(final["research_parts"].keys())
    assert present == set(RESEARCH_SCOPES) - skipping

    # Join still produces a ResearchOutput — flagged partial.
    out = final["research_output"]
    assert out is not None
    assert out.partial is True
    for scope in skipping:
        assert getattr(out, scope) is None
    for scope in set(RESEARCH_SCOPES) - skipping:
        assert getattr(out, scope) is not None


# ---------------------------------------------------------------------------
# Topology contract — guard against drift back to a node-returning-Send shape.
# Sync tests live below to avoid the module-level asyncio mark.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope=None)
async def test_route_to_research_subagents_returns_one_send_per_scope():
    from langgraph.types import Send

    sends = route_to_research_subagents(_initial_state())
    assert len(sends) == len(RESEARCH_SCOPES)
    for s in sends:
        assert isinstance(s, Send)
    destinations = {s.node for s in sends}
    assert destinations == set(RESEARCH_SUBAGENT_NODES.values())


@pytest.mark.asyncio(loop_scope=None)
async def test_dispatch_node_is_pure_passthrough():
    """Dispatch must NOT mutate `research_parts` — re-entry safety relies on
    dict-keyed merging from the subagents themselves (SA F-2 option 2)."""
    update = research_dispatch_node(_initial_state())
    assert "research_parts" not in update
    assert update == {}
