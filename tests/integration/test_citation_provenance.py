"""Sprint 3 3.B.5 — citation provenance enforcement.

End-to-end-ish exercise of `validate_law_citations`: build a `LawResearch`
that mixes verifiable + hallucinated citations, point the validator at
the set of `source_id`s the run actually retrieved, and assert that
hallucinations end up in `suppressed_citations` while verified ones pass
through unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.pipeline.graph.output_validator import validate_law_citations
from src.pipeline.graph.schemas import (
    LawResearch,
    LegalElement,
    LegalRule,
    Precedent,
    PrecedentProvenance,
    SuppressedCitation,
)


def _provenance() -> PrecedentProvenance:
    return PrecedentProvenance(
        source="vector_store",
        query="liability",
        retrieved_at=datetime.now(UTC),
    )


def _law(
    rules: list[LegalRule],
    precedents: list[Precedent],
    suppressed: list[SuppressedCitation] | None = None,
) -> LawResearch:
    return LawResearch(
        legal_rules=rules,
        precedents=precedents,
        precedent_source_metadata=_provenance(),
        legal_elements_checklist=[],
        suppressed_citations=suppressed or [],
    )


VALID_RULE = LegalRule(
    rule_id="r-1",
    jurisdiction="SG",
    citation="Small Claims Tribunals Act s.5",
    text="…",
    applicability="…",
    supporting_sources=["file-stat-001:abcdef012345"],
)

HALLUCINATED_RULE = LegalRule(
    rule_id="r-2",
    jurisdiction="SG",
    citation="Made-Up Statute s.99",
    text="…",
    applicability="…",
    supporting_sources=[],
)

VALID_PRECEDENT = Precedent(
    case_name="Tan v Tan",
    citation="[2020] SGHC 1",
    jurisdiction="SG",
    holding="…",
    relevance_rationale="…",
    supporting_sources=["file-case-9:fedcba543210"],
)

HALLUCINATED_PRECEDENT = Precedent(
    case_name="Fictitious v Fictitious",
    citation="[1999] SGCA 999",
    jurisdiction="SG",
    holding="…",
    relevance_rationale="…",
    supporting_sources=["file-fake:000000000000"],
)

RETRIEVED = {"file-stat-001:abcdef012345", "file-case-9:fedcba543210"}


def test_valid_citations_pass_through_unchanged():
    law = _law([VALID_RULE], [VALID_PRECEDENT])
    out = validate_law_citations(law, RETRIEVED)
    assert [r.rule_id for r in out.legal_rules] == ["r-1"]
    assert [p.case_name for p in out.precedents] == ["Tan v Tan"]
    assert out.suppressed_citations == []


def test_hallucinated_rule_suppressed_with_reason():
    law = _law([VALID_RULE, HALLUCINATED_RULE], [])
    out = validate_law_citations(law, RETRIEVED)
    assert [r.rule_id for r in out.legal_rules] == ["r-1"]
    assert len(out.suppressed_citations) == 1
    suppressed = out.suppressed_citations[0]
    assert suppressed.citation_text == "Made-Up Statute s.99"
    assert suppressed.reason == "no_source_match"


def test_hallucinated_precedent_suppressed_with_reason():
    law = _law([], [VALID_PRECEDENT, HALLUCINATED_PRECEDENT])
    out = validate_law_citations(law, RETRIEVED)
    assert [p.case_name for p in out.precedents] == ["Tan v Tan"]
    assert len(out.suppressed_citations) == 1
    suppressed = out.suppressed_citations[0]
    assert suppressed.citation_text == "[1999] SGCA 999"
    assert suppressed.reason == "no_source_match"


def test_existing_suppressed_entries_preserved():
    pre_existing = SuppressedCitation(citation_text="prev", reason="expired_statute")
    law = _law([HALLUCINATED_RULE], [], suppressed=[pre_existing])
    out = validate_law_citations(law, RETRIEVED)
    reasons = [s.reason for s in out.suppressed_citations]
    assert reasons == ["expired_statute", "no_source_match"]


def test_partial_overlap_keeps_citation():
    """A rule cited with multiple sources passes if at least one is verified."""
    rule = LegalRule(
        rule_id="r-mixed",
        jurisdiction="SG",
        citation="Real Act s.1",
        text="…",
        applicability="…",
        supporting_sources=["file-stat-001:abcdef012345", "file-fake:000000000000"],
    )
    law = _law([rule], [])
    out = validate_law_citations(law, RETRIEVED)
    assert [r.rule_id for r in out.legal_rules] == ["r-mixed"]
    assert out.suppressed_citations == []


def test_empty_supporting_sources_suppressed():
    law = _law([HALLUCINATED_RULE], [])
    out = validate_law_citations(law, set())
    assert out.legal_rules == []
    assert out.suppressed_citations[0].reason == "no_source_match"


def test_input_law_research_not_mutated():
    law = _law([VALID_RULE, HALLUCINATED_RULE], [VALID_PRECEDENT, HALLUCINATED_PRECEDENT])
    _ = validate_law_citations(law, RETRIEVED)
    # Original payload still has the hallucinations
    assert len(law.legal_rules) == 2
    assert len(law.precedents) == 2
    assert law.suppressed_citations == []


def test_unused_legal_elements_passthrough():
    """The validator only filters citations; element checklist passes through."""
    elements = [LegalElement(element="duty", satisfied=True, rationale="…")]
    law = LawResearch(
        legal_rules=[VALID_RULE],
        precedents=[VALID_PRECEDENT],
        precedent_source_metadata=_provenance(),
        legal_elements_checklist=elements,
        suppressed_citations=[],
    )
    out = validate_law_citations(law, RETRIEVED)
    assert out.legal_elements_checklist == elements
