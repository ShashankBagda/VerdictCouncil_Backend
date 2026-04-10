---
updated: 2026-04-09T16:30:00Z
sources:
  - ../../../docs/architecture/01-user-stories.md
  - ../../../specs/user-stories-sync.md
---
# User Stories §1.9 — Hearing & Post-Decision

Three stories extending the hearing pack into hearing time and the
post-decision lifecycle (amendment + reopen). Drafted in
[specs/user-stories-sync.md](../../../specs/user-stories-sync.md) and added
in the 2026-04-09 sync round.

See the canonical text in [docs/architecture/01-user-stories.md](../../../docs/architecture/01-user-stories.md).
Index: [user-stories-index](user-stories-index.md).

---

## US-035 — Take In-Hearing Notes

**Actor:** Judge

> As a judicial officer, I want to take notes during the hearing that are
> anchored to specific items in the hearing pack, so that my
> contemporaneous observations are captured alongside the AI analysis and
> can be incorporated into the recorded decision.

Hearing-mode lock on the pack from
[US-020](user-stories-5-arguments-deliberation.md), per-item annotations,
probative vs administrative tagging, offline-resilient sync. Probative
notes feed [US-025](user-stories-6-verdict-governance.md) decision recording.

## US-036 — Amend a Recorded Decision

**Actor:** Judge

> As a judicial officer, I want to amend a previously recorded judicial
> decision with a clear amendment trail, so that corrections or
> post-hearing updates are reflected without erasing the original record.

Amendment types: `clerical_correction`, `post_hearing_update`,
`error_correction`. Original decision preserved; amendments form an
auditable chain. Recording judge or senior judge only.

## US-037 — Reopen a Closed Case

**Actor:** Judge (with senior judge approval)

> As a judicial officer, I want to reopen a closed case with appropriate
> justification and approval, so that new evidence, appeals, or clerical
> errors can be addressed without losing the original case history.

Reopen request with reason (`new_evidence`, `appeal`, `clerical_error`,
`procedural_defect`), routed to senior judge inbox for approval. Pipeline
re-trigger via [US-005](user-stories-1-intake.md). Original closure record
preserved.

> The senior judge inbox itself is **deferred** to a follow-up story
> (US-040 — Senior Judge — Review Referred Cases) covered in the next steps
> of [specs/user-stories-sync.md](../../../specs/user-stories-sync.md).
