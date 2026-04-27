"""Citation provenance enforcement (Sprint 3 3.B.5).

Pure validator: given a `LawResearch` payload and the set of `source_id`s
that the run actually retrieved (the union of every `Document.metadata`
on the run's tool-artifact chain), filter out rules/precedents whose
self-reported `supporting_sources` cannot be verified against what the
tools returned. Each suppressed citation is recorded in
`LawResearch.suppressed_citations` with a `SuppressionReason` enum so the
auditor and gate-review UI can show why.

Suppression policy (3.B.5):
- A citation is **valid** when at least one entry in
  `supporting_sources` matches the run's retrieved set.
- An empty `supporting_sources` or no matches → suppress with
  `no_source_match`.
- Other reasons (`low_score`, `expired_statute`, `out_of_jurisdiction`)
  are placeholders for downstream validators; they live in the same enum
  but are not produced by this function.

Wiring into `research_join` requires state-level source_id aggregation,
which is a follow-up; this function is invoked directly from the
integration test today so 3.B.6/3.B.7 can ride on top.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.pipeline.graph.schemas import (
    LawResearch,
    LegalRule,
    Precedent,
    SuppressedCitation,
)


def _is_supported(
    supporting_sources: list[str],
    retrieved_full: set[str],
    retrieved_file_ids: set[str],
) -> bool:
    """A citation is supported when any claimed source matches retrieval.

    The retrieval layer stamps `source_id = "<file_id>:<content_hash>"` on
    every Document (`tools.py:104,135`). Agents reliably copy the `file_id`
    portion but not the chunk-level `content_hash`, so an exact-membership
    check would suppress every well-grounded citation. We accept either:
    a full `source_id` match (chunk-level provenance) or a `file_id`-only
    match (document-level provenance). Both still verify the rule traces
    to a document the run actually retrieved — the no-hallucination guard
    that empty `supporting_sources` already enforces stays intact.
    """
    for src in supporting_sources:
        if src in retrieved_full:
            return True
        head = src.split(":", 1)[0]
        if head in retrieved_file_ids:
            return True
    return False


def validate_law_citations(
    law: LawResearch,
    retrieved_source_ids: Iterable[str],
) -> LawResearch:
    """Drop rules/precedents whose supporting_sources don't match any
    retrieved source_id and log the drop in `suppressed_citations`.

    The input is not mutated. Returns a new `LawResearch` with filtered
    lists and the suppressed list appended (preserving any pre-existing
    entries the agent itself produced).
    """
    retrieved_full = set(retrieved_source_ids)
    retrieved_file_ids = {sid.split(":", 1)[0] for sid in retrieved_full}
    kept_rules: list[LegalRule] = []
    kept_precedents: list[Precedent] = []
    suppressed: list[SuppressedCitation] = list(law.suppressed_citations)

    for rule in law.legal_rules:
        if _is_supported(rule.supporting_sources, retrieved_full, retrieved_file_ids):
            kept_rules.append(rule)
        else:
            suppressed.append(
                SuppressedCitation(citation_text=rule.citation, reason="no_source_match")
            )

    for precedent in law.precedents:
        if _is_supported(precedent.supporting_sources, retrieved_full, retrieved_file_ids):
            kept_precedents.append(precedent)
        else:
            suppressed.append(
                SuppressedCitation(citation_text=precedent.citation, reason="no_source_match")
            )

    return law.model_copy(
        update={
            "legal_rules": kept_rules,
            "precedents": kept_precedents,
            "suppressed_citations": suppressed,
        }
    )
