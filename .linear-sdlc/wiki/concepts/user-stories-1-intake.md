---
updated: 2026-04-09T16:30:00Z
sources:
  - ../../../docs/architecture/01-user-stories.md
---
# User Stories §1.1 — Case Intake & Setup

Five stories covering the start of a case lifecycle: how a judge submits
documents, how the pipeline gets triggered, and how the system handles
acceptance, rejection, and supplementary uploads.

See the canonical text in [docs/architecture/01-user-stories.md](../../../docs/architecture/01-user-stories.md)
for full acceptance criteria and happy flows. Index: [user-stories-index](user-stories-index.md).

---

## US-001 — Upload New Case

**Actor:** Judge

> As a judicial officer, I want to upload case documents for AI processing,
> so that VerdictCouncil can analyse the case and provide decision-support
> recommendations.

Domain-specific intake (SCT claim amount, Traffic offence code), OpenAI
Files API storage, and automatic dispatch to Agent 1.

## US-002 — View Document Processing Status

**Actor:** Judge

> As a judicial officer, I want to monitor real-time pipeline progress
> across the 9 agents, so that I know when analysis is complete and can
> identify any stalled or failed stages.

Live status across all 9 agent stages, retry on failure, polling or SSE.

## US-003 — Receive Jurisdiction Validation Result

**Actor:** Judge

> As a judicial officer, I want to see whether a case passes jurisdiction
> checks, so that I can confirm the tribunal has authority to hear the
> matter before investing time in full analysis.

SCT claim-amount and Traffic offence-code rules, with pass/fail/warning
states and statutory citations on failure.

## US-004 — Handle Rejected Cases

**Actor:** Judge

> As a judicial officer, I want to view rejection reasons and optionally
> override them with justification, so that I retain final authority over
> case acceptance while understanding the AI's reasoning.

Override workflow with mandatory written justification, audit trail entry,
and pipeline resumption from the point of rejection.

## US-005 — Re-upload or Add Documents to Existing Case

**Actor:** Judge

> As a judicial officer, I want to add supplementary documents to an
> existing case after initial upload, so that late-arriving evidence or
> corrected filings are incorporated into the analysis.

Selective re-trigger of only the affected pipeline stages; document version
history; preservation of unaffected analysis. **Currently a backlog gap —
no Linear ticket exists yet.**
