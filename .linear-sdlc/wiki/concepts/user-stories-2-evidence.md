---
updated: 2026-04-09T16:30:00Z
sources:
  - ../../../docs/architecture/01-user-stories.md
---
# User Stories §1.2 — Evidence & Facts

Five stories covering evidence analysis, fact reconstruction, source
drill-down, dispute identification, and gap analysis. Outputs come from
Agents 3 and 4.

See the canonical text in [docs/architecture/01-user-stories.md](../../../docs/architecture/01-user-stories.md).
Index: [user-stories-index](user-stories-index.md).

---

## US-006 — Review Evidence Analysis Dashboard

**Actor:** Judge

> As a judicial officer, I want to review a dashboard showing per-item
> evidence strength, admissibility flags, contradictions, gaps, and
> corroborations, so that I can quickly assess the evidential landscape of
> the case.

Per-item strength rating, admissibility flags, contradiction/corroboration
links, and filterable evidence list.

## US-007 — View Fact Timeline

**Actor:** Judge

> As a judicial officer, I want to view a chronological timeline of
> extracted facts with source citations and confidence ratings, so that I
> can understand the sequence of events and identify areas of factual
> dispute.

Chronological timeline, agreed/disputed/unilateral classification, source
citation per fact, click-through to underlying document.

## US-008 — Drill Down to Source Document

**Actor:** Judge

> As a judicial officer, I want to click any cited reference and see the
> original document excerpt highlighted alongside the AI extraction, so
> that I can verify the AI's interpretation against the source material.

Split-pane view with highlighted passage; document metadata (filename,
upload date, page number).

## US-009 — Flag Disputed Facts

**Actor:** Judge

> As a judicial officer, I want to see all facts where parties disagree,
> with both versions presented side-by-side and linked evidence, so that I
> can identify the core disputes requiring resolution at the hearing.

Dispute view with side-by-side party versions, impact ranking, and
annotation support.

## US-010 — Review Evidence Gaps

**Actor:** Judge

> As a judicial officer, I want to see what evidence is expected but
> missing, linked to legal requirements, with an impact assessment, so that
> I can understand potential weaknesses in each party's case and direct
> enquiries appropriately.

Gap surfaced per legal element, burden-of-proof attribution, and
critical/significant/minor impact rating.
