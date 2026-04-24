---
updated: 2026-04-09T18:00:00Z
sources:
  - ../../../docs/architecture/01-user-stories.md
  - ../../../specs/user-stories-sync.md
---
# User Stories §1.10 — Senior Judge Operations

A single story (so far) introducing the **Senior Judge** persona's primary
work surface: a unified inbox for everything routed to senior-judge action.
Drafted as a follow-up to the 2026-04-09 sync round; closes the persona gap
referenced in [§1.6](user-stories-6-verdict-governance.md) (US-024) and
[§1.9](user-stories-9-hearing-post-decision.md) (US-036, US-037).

See the canonical text in [docs/architecture/01-user-stories.md](../../../docs/architecture/01-user-stories.md).
Index: [user-stories-index](user-stories-index.md).

> **US-038 and US-039 are intentionally reserved** for follow-up
> senior-judge stories (e.g., bulk-action on inbox, senior-judge analytics
> dashboard) that are out of scope for the MVP.

---

## US-040 — Senior Judge — Review Referred Cases

**Actor:** Senior Judge

> As a senior judicial officer, I want a single inbox that surfaces every
> item routed to me for senior-judge action — escalation referrals,
> decision amendments by other judges, and reopen requests — so that I can
> review, approve, reject, or reassign each one without hunting through
> individual cases.

The inbox aggregates three referral sources:

1. **Escalations** referred from [US-024](user-stories-6-verdict-governance.md)
   when a regular judge clicks "Refer to Senior Judge" on an escalated case.
2. **Reopen requests** from [US-037](user-stories-9-hearing-post-decision.md)
   awaiting senior approval.
3. **Amendments-of-record** from [US-036](user-stories-9-hearing-post-decision.md)
   when the amending judge is not the original recording judge.

Per-entry actions: **Approve**, **Reject** (with reason), **Reassign** to
another senior judge, **Request more info** (returns to originating judge).
A **two-person rule** prevents a senior judge from approving their own
referral.

Gated by the `senior_judge` role assigned via
[US-033](user-stories-8-administration.md#us-033--manage-user-accounts-and-roles).
All actions are recorded in the audit trail
([US-026](user-stories-7-audit-export-session.md)) and reflected in the
dashboard counts ([US-029](user-stories-7-audit-export-session.md)).
Notifications are **in-app only** for the MVP.
