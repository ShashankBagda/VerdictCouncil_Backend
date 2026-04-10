---
updated: 2026-04-09T16:30:00Z
sources:
  - ../../../docs/architecture/01-user-stories.md
  - ../../../specs/user-stories-sync.md
---
# User Stories §1.8 — Administration & Operations

Four stories introducing the **Platform Administrator / Ops Engineer**
persona. These stories were drafted in
[specs/user-stories-sync.md](../../../specs/user-stories-sync.md) and added
in the 2026-04-09 sync round.

See the canonical text in [docs/architecture/01-user-stories.md](../../../docs/architecture/01-user-stories.md).
Index: [user-stories-index](user-stories-index.md).

---

## US-031 — Refresh / Re-index Vector Stores

**Actor:** Platform Administrator

> As a platform administrator, I want to trigger a refresh of the SCT and
> Traffic vector stores, so that the legal knowledge used by Agent 6 stays
> current as statutes, judgments, and practice directions are added or
> amended.

Per-store or all-stores refresh as a background job, with diff report,
audit trail entry, and visible knowledge-base metadata refresh on the
judge view ([US-017](user-stories-4-legal-research.md)).

## US-032 — Monitor Agent & Pipeline Health

**Actor:** Ops Engineer

> As an ops engineer, I want to see real-time health metrics for each of
> the 9 agents and the message bus, so that I can detect degradation early
> and respond before it affects judges.

Per-agent latency / success-rate / failure tiles, Solace queue depth,
dead-letter alerting, click-through to recent failure traces. Gated by
admin role ([US-033](user-stories-8-administration.md#us-033--manage-user-accounts-and-roles)).

## US-033 — Manage User Accounts and Roles

**Actor:** Platform Administrator

> As an administrator, I want to create, update, deactivate, and assign
> roles to user accounts, so that only authorized judicial officers and
> staff can access the system and their access matches their
> responsibilities.

CRUD on accounts, password reset, role assignment (`judge`,
`senior_judge`, `admin`), role-based route access. All changes audit-logged.

## US-034 — Configure Cost and Quota Alerts

**Actor:** Platform Administrator

> As an administrator, I want to configure OpenAI API spend thresholds and
> per-judge quotas, so that the organization stays within its budget and
> no single user can exhaust capacity.

Monthly cap with warning thresholds, per-user quotas (cases or dollars),
hard-cap enforcement with override capability, top-spender dashboard.
