---
updated: 2026-04-09T16:30:00Z
sources:
  - ../../../docs/architecture/01-user-stories.md
---
# User Stories §1.4 — Legal Research

Four stories covering Agent 6 (Legal Knowledge): statute matching,
precedent retrieval (curated + live PAIR Search), and knowledge base
status visibility.

See the canonical text in [docs/architecture/01-user-stories.md](../../../docs/architecture/01-user-stories.md).
Index: [user-stories-index](user-stories-index.md).

---

## US-014 — Review Applicable Statutes

**Actor:** Judge

> As a judicial officer, I want to review matched statutory provisions with
> verbatim text, relevance scores, and application to case facts, so that I
> can confirm the legal framework applicable to the case.

Vector store sourced (`vs_sct` or `vs_traffic` per domain), relevance-ranked,
with verbatim text and case-specific application narrative.

## US-015 — Review Precedent Cases

**Actor:** Judge

> As a judicial officer, I want to review similar past cases with
> citations, outcomes, reasoning, similarity scores, and distinguishing
> factors, so that I can ensure consistency with established jurisprudence.

Both pro-claimant and pro-respondent precedents, tagged `curated` or
`live_search`, with distinguishing factors.

## US-016 — Search Live Precedent Database

**Actor:** Judge

> As a judicial officer, I want to trigger a live search of the PAIR Search
> API (search.pair.gov.sg), so that I can access binding higher court case
> law from eLitigation beyond the curated vector store.

PAIR Search integration via the `search_precedents` tool on Agent 6;
results integrate with US-015 listings. Note: PAIR covers higher courts
(SGHC, SGCA), not SCT or lower State Courts.

## US-017 — View Knowledge Base Status

**Actor:** Judge

> As a judicial officer, I want to see vector store metadata and health
> status, so that I can have confidence in the currency and completeness
> of the legal knowledge underpinning the analysis.

Doc count, last-updated SGT timestamp, and healthy/degraded/unavailable
status per vector store. Stale-data warnings surface on the case view.
