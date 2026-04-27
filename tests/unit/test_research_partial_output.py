"""Sprint 1 1.A1.5 — partial-output handling for `ResearchOutput.from_parts`.

When 1-3 of 4 research subagents fail to return a part, the join node must
still produce a valid `ResearchOutput` with `partial=True`. Missing scopes
land as `None` in the merged output; the gate2 UI surfaces the flag to
the judge.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.pipeline.graph.research import research_join_node
from src.pipeline.graph.schemas import (
    EvidenceResearch,
    FactsResearch,
    LawResearch,
    PrecedentProvenance,
    ResearchOutput,
    ResearchPart,
    WitnessesResearch,
)


def _evidence_part() -> ResearchPart:
    return ResearchPart(scope="evidence", evidence=EvidenceResearch(evidence_items=[]))


def _facts_part() -> ResearchPart:
    return ResearchPart(scope="facts", facts=FactsResearch(facts=[], timeline=[]))


def _witnesses_part() -> ResearchPart:
    return ResearchPart(scope="witnesses", witnesses=WitnessesResearch(witnesses=[]))


def _law_part() -> ResearchPart:
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


# ---------------------------------------------------------------------------
# from_parts — direct schema-level coverage
# ---------------------------------------------------------------------------


def test_from_parts_all_four_present_is_not_partial():
    parts = {
        "evidence": _evidence_part(),
        "facts": _facts_part(),
        "witnesses": _witnesses_part(),
        "law": _law_part(),
    }
    merged = ResearchOutput.from_parts(parts)
    assert merged.partial is False
    assert merged.evidence is not None
    assert merged.facts is not None
    assert merged.witnesses is not None
    assert merged.law is not None


@pytest.mark.parametrize(
    "missing_scope",
    ["evidence", "facts", "witnesses", "law"],
)
def test_from_parts_one_missing_marks_partial(missing_scope: str):
    all_parts = {
        "evidence": _evidence_part(),
        "facts": _facts_part(),
        "witnesses": _witnesses_part(),
        "law": _law_part(),
    }
    del all_parts[missing_scope]
    merged = ResearchOutput.from_parts(all_parts)
    assert merged.partial is True
    assert getattr(merged, missing_scope) is None
    for present in {"evidence", "facts", "witnesses", "law"} - {missing_scope}:
        assert getattr(merged, present) is not None


def test_from_parts_only_one_scope_present_is_partial():
    merged = ResearchOutput.from_parts({"evidence": _evidence_part()})
    assert merged.partial is True
    assert merged.evidence is not None
    assert merged.facts is None
    assert merged.witnesses is None
    assert merged.law is None


def test_from_parts_empty_dict_is_partial_with_all_none():
    merged = ResearchOutput.from_parts({})
    assert merged.partial is True
    assert merged.evidence is None
    assert merged.facts is None
    assert merged.witnesses is None
    assert merged.law is None


# ---------------------------------------------------------------------------
# research_join_node — node-level coverage (reads from state)
# ---------------------------------------------------------------------------


def test_join_node_with_no_research_parts_yields_partial_output():
    """Join must tolerate `research_parts` absent or empty (all subagents failed)."""
    update = research_join_node({})  # no `research_parts` key at all
    assert "research_output" in update
    out = update["research_output"]
    assert out.partial is True
    assert out.evidence is None and out.facts is None
    assert out.witnesses is None and out.law is None


def test_join_node_with_three_of_four_parts_yields_partial_output():
    state = {
        "research_parts": {
            "evidence": _evidence_part(),
            "facts": _facts_part(),
            "law": _law_part(),
        },
    }
    update = research_join_node(state)
    out = update["research_output"]
    assert out.partial is True
    assert out.witnesses is None
    assert out.evidence is not None
    assert out.facts is not None
    assert out.law is not None


def test_join_node_with_all_four_parts_yields_complete_output():
    state = {
        "research_parts": {
            "evidence": _evidence_part(),
            "facts": _facts_part(),
            "witnesses": _witnesses_part(),
            "law": _law_part(),
        },
    }
    update = research_join_node(state)
    out = update["research_output"]
    assert out.partial is False
    assert out.evidence is not None
    assert out.facts is not None
    assert out.witnesses is not None
    assert out.law is not None


def test_join_node_with_explicit_none_research_parts_falls_back_to_empty():
    """Some checkpoint states may serialize `research_parts` as None; join must cope."""
    update = research_join_node({"research_parts": None})
    out = update["research_output"]
    assert out.partial is True
