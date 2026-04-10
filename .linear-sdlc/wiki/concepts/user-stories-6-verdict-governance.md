---
updated: 2026-04-09T16:30:00Z
sources:
  - ../../../docs/architecture/01-user-stories.md
---
# User Stories §1.6 — Verdict & Governance

Four stories covering Agent 9 (Governance & Verdict): the verdict
recommendation, automated fairness audit, escalation handling, and the
judge's final recorded decision.

See the canonical text in [docs/architecture/01-user-stories.md](../../../docs/architecture/01-user-stories.md).
Index: [user-stories-index](user-stories-index.md).

---

## US-022 — Review Verdict Recommendation

**Actor:** Judge

> As a judicial officer, I want to review the AI's verdict recommendation
> with a confidence score, so that I have a structured starting point for
> my judicial decision-making.

SCT recommendations include order type and amount; Traffic recommendations
include verdict and proposed sentence. Always labelled **"RECOMMENDATION —
Subject to Judicial Determination"**.

## US-023 — Review Fairness and Bias Audit

**Actor:** Judge

> As a judicial officer, I want to review a governance audit checking for
> balance, unsupported claims, logical fallacies, bias, and evidence
> completeness, so that I can be confident the AI's analysis is fair and
> methodologically sound.

Automated, non-skippable audit. Each check returns pass/warning/fail with
explanation. Critical fails block proceeding without acknowledgement.

## US-024 — Handle Escalated Cases

**Actor:** Judge

> As a judicial officer, I want to review cases that have been escalated by
> the AI agents, so that I can apply human judgment to matters the system
> has identified as requiring special attention.

Escalations come from Agent 2 (complexity threshold) or Agent 9 (governance
concern). Judge can continue, re-process with adjusted params, refer to
senior judge, or proceed without AI support. **Backend endpoint is
currently a backlog gap** — only the FE inbox ticket exists.

## US-025 — Record Judicial Decision

**Actor:** Judge

> As a judicial officer, I want to record my actual decision — accepting,
> modifying, or rejecting the recommendation — with reasoning, so that
> there is a clear record of the judicial outcome alongside the AI
> recommendation.

Three decision types: `accept_as_is`, `modify`, `reject`. Modify and reject
require written reasoning. Decision is immutable except via the formal
amendment flow ([US-036](user-stories-9-hearing-post-decision.md)).
