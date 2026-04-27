"""Sprint 3 3.B.5 wiring — research_join validates law citations.

The pure validator landed in `output_validator.validate_law_citations`.
This test exercises the in-graph integration: research subagents
contribute `retrieved_source_ids` (extracted from their tool-message
artifacts), and `research_join_node` runs the validator against
`research_output.law` so hallucinated citations are suppressed before
the gate2 review surface ever sees them.
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
    LegalRule,
    Precedent,
    PrecedentProvenance,
    ResearchPart,
    WitnessesResearch,
)
from src.pipeline.graph.state import GraphState
from src.shared.case_state import CaseState

pytestmark = pytest.mark.asyncio


VALID_FILE_ID = "file-real:abcdef012345"
HALLUCINATED_FILE_ID = "file-fake:000000000000"


def _law_with_mixed_citations() -> LawResearch:
    return LawResearch(
        legal_rules=[
            LegalRule(
                rule_id="r-real",
                jurisdiction="SG",
                citation="Real Statute s.1",
                text="…",
                applicability="…",
                supporting_sources=[VALID_FILE_ID],
            ),
            LegalRule(
                rule_id="r-fake",
                jurisdiction="SG",
                citation="Made-Up Statute s.99",
                text="…",
                applicability="…",
                supporting_sources=[],
            ),
        ],
        precedents=[
            Precedent(
                case_name="Tan v Tan",
                citation="[2020] SGHC 1",
                jurisdiction="SG",
                holding="…",
                relevance_rationale="…",
                supporting_sources=[VALID_FILE_ID],
            ),
            Precedent(
                case_name="Fictitious v Fictitious",
                citation="[1999] SGCA 999",
                jurisdiction="SG",
                holding="…",
                relevance_rationale="…",
                supporting_sources=[HALLUCINATED_FILE_ID],
            ),
        ],
        precedent_source_metadata=PrecedentProvenance(
            source="vector_store",
            query="liability",
            retrieved_at=datetime(2026, 4, 25, 0, 0, 0),
        ),
        legal_elements_checklist=[],
        suppressed_citations=[],
    )


def _stub_part(scope: str, *, law: LawResearch | None = None) -> ResearchPart:
    if scope == "evidence":
        return ResearchPart(
            scope="evidence", evidence=EvidenceResearch(evidence_items=[], credibility_scores={})
        )
    if scope == "facts":
        return ResearchPart(scope="facts", facts=FactsResearch(facts=[], timeline=[]))
    if scope == "witnesses":
        return ResearchPart(
            scope="witnesses", witnesses=WitnessesResearch(witnesses=[], credibility={})
        )
    if scope == "law":
        return ResearchPart(scope="law", law=law)
    raise ValueError(f"unknown scope {scope!r}")


def _make_subagent(scope: str, source_ids: list[str], *, law: LawResearch | None = None):
    async def _node(_state: dict[str, Any]) -> dict[str, Any]:
        update: dict[str, Any] = {"research_parts": {scope: _stub_part(scope, law=law)}}
        if source_ids:
            update["retrieved_source_ids"] = {scope: list(source_ids)}
        return update

    _node.__name__ = f"stub_research_{scope}"
    return _node


def _build_graph(*, law: LawResearch, source_ids_by_scope: dict[str, list[str]]):
    g = StateGraph(GraphState)
    g.add_node("research_dispatch", research_dispatch_node)
    for scope in RESEARCH_SCOPES:
        scope_law = law if scope == "law" else None
        g.add_node(
            RESEARCH_SUBAGENT_NODES[scope],
            _make_subagent(scope, source_ids_by_scope.get(scope, []), law=scope_law),
        )
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


def _initial_state() -> GraphState:
    return {
        "case": CaseState(case_id="00000000-0000-0000-0000-000000000099"),
        "run_id": "run-validate",
        "extra_instructions": {},
        "retry_counts": {},
        "halt": None,
        "research_parts": {},
        "research_output": None,
        "retrieved_source_ids": {},
        "is_resume": False,
        "start_agent": None,
    }


async def test_research_join_suppresses_hallucinated_citations():
    law = _law_with_mixed_citations()
    graph = _build_graph(
        law=law,
        source_ids_by_scope={"law": [VALID_FILE_ID]},
    )

    final = await graph.ainvoke(
        _initial_state(),
        config={"configurable": {"thread_id": "validate-1"}},
    )

    out = final["research_output"]
    assert out is not None
    assert out.law is not None

    rule_ids = [r.rule_id for r in out.law.legal_rules]
    case_names = [p.case_name for p in out.law.precedents]
    suppressed_texts = [s.citation_text for s in out.law.suppressed_citations]

    assert rule_ids == ["r-real"]
    assert case_names == ["Tan v Tan"]
    # Both hallucinated entries dropped:
    assert "Made-Up Statute s.99" in suppressed_texts
    assert "[1999] SGCA 999" in suppressed_texts


async def test_research_join_passes_through_when_all_citations_verifiable():
    law = LawResearch(
        legal_rules=[
            LegalRule(
                rule_id="r-1",
                jurisdiction="SG",
                citation="Real s.1",
                text="…",
                applicability="…",
                supporting_sources=[VALID_FILE_ID],
            )
        ],
        precedents=[],
        precedent_source_metadata=PrecedentProvenance(
            source="vector_store",
            query="x",
            retrieved_at=datetime(2026, 4, 25, 0, 0, 0),
        ),
        legal_elements_checklist=[],
        suppressed_citations=[],
    )
    graph = _build_graph(law=law, source_ids_by_scope={"law": [VALID_FILE_ID]})

    final = await graph.ainvoke(
        _initial_state(),
        config={"configurable": {"thread_id": "validate-2"}},
    )
    out = final["research_output"]
    assert [r.rule_id for r in out.law.legal_rules] == ["r-1"]
    assert out.law.suppressed_citations == []


async def test_research_join_handles_missing_law_part():
    """If the law subagent contributed no LawResearch, the join must not crash."""
    graph = _build_graph(law=None, source_ids_by_scope={})

    final = await graph.ainvoke(
        _initial_state(),
        config={"configurable": {"thread_id": "validate-3"}},
    )
    out = final["research_output"]
    assert out is not None
    assert out.law is None  # nothing to validate
