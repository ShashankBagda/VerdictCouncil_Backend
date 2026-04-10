---
updated: 2026-04-09T16:30:00Z
sources:
  - ../../../docs/architecture/01-user-stories.md
---
# User Stories §1.3 — Witness Analysis

Three stories covering Agent 5 (Witness Analysis): credibility scoring,
anticipated testimony for traffic cases, and AI-suggested judicial
questions.

See the canonical text in [docs/architecture/01-user-stories.md](../../../docs/architecture/01-user-stories.md).
Index: [user-stories-index](user-stories-index.md).

---

## US-011 — Review Witness Profiles and Credibility Scores

**Actor:** Judge

> As a judicial officer, I want to review each witness's identification,
> role, bias indicators, and credibility score with a breakdown, so that I
> can assess witness reliability and prepare targeted questioning.

Per-witness profile, 0–100 credibility score with consistency / corroboration
/ bias / specificity factor breakdown, evidence basis per factor.

## US-012 — View Anticipated Testimony (Traffic Only)

**Actor:** Judge

> As a judicial officer, I want to view simulated testimony summaries based
> on written statements for traffic cases, so that I can prepare for the
> hearing by anticipating the likely evidence to be given.

Traffic-only feature; AI-simulated testimony with vulnerability flags, all
clearly marked **"Simulated — For Judicial Preparation Only"**.

## US-013 — Review Suggested Judicial Questions

**Actor:** Judge

> As a judicial officer, I want to review AI-generated probing questions
> tagged by type and linked to case weaknesses, so that I can conduct a
> thorough and focused hearing.

Editable question list, tagged by type (factual_clarification, evidence_gap,
credibility_probe, legal_interpretation), exportable into the hearing pack.
