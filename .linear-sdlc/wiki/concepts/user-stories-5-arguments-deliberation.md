---
updated: 2026-04-09T16:30:00Z
sources:
  - ../../../docs/architecture/01-user-stories.md
---
# User Stories §1.5 — Arguments & Deliberation

Four stories covering Agents 7 (Argument Construction) and 8 (Deliberation):
balanced argument construction, reasoning chain visibility, hearing pack
preparation, and alternative outcome comparison.

See the canonical text in [docs/architecture/01-user-stories.md](../../../docs/architecture/01-user-stories.md).
Index: [user-stories-index](user-stories-index.md).

---

## US-018 — Review Both Sides' Arguments

**Actor:** Judge

> As a judicial officer, I want to review the arguments for both sides with
> strength comparisons and weaknesses noted, so that I can approach the
> hearing with a balanced understanding of each party's position.

SCT cases get balanced strength comparisons; Traffic cases use a
prosecution-vs-defence structure. All marked **"Internal Analysis for
Judicial Review Only"**.

## US-019 — Review Deliberation Reasoning Chain

**Actor:** Judge

> As a judicial officer, I want to follow the step-by-step reasoning from
> evidence to preliminary conclusion, so that I can evaluate the AI's
> analytical process and identify any logical weaknesses.

Numbered reasoning steps grouped into Factual Findings / Legal Analysis /
Preliminary Conclusion, with confidence flags and an Uncertainty Factors
list.

## US-020 — Prepare Hearing Pack

**Actor:** Judge

> As a judicial officer, I want to access a consolidated pre-hearing
> summary with key facts, legal issues, suggested questions, and weak
> points per side, so that I can walk into the hearing fully prepared.

Single consolidated document drawing from all prior agent outputs;
annotation, custom items, checklist mode, and PDF export. Extended at
hearing time by [US-035](user-stories-9-hearing-post-decision.md).

## US-021 — Compare Alternative Outcomes

**Actor:** Judge

> As a judicial officer, I want to see the recommended verdict alongside at
> least one alternative with reasoning, so that I can consider different
> outcomes and understand what factors could shift the result.

Recommended + at least one alternative outcome with confidence scores and
explicit pivot factors. On-demand "what if" alternatives.
