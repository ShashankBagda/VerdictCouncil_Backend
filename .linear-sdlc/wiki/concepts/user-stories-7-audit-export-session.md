---
updated: 2026-04-09T16:30:00Z
sources:
  - ../../../docs/architecture/01-user-stories.md
---
# User Stories §1.7 — Audit, Export & Session

Five stories covering audit trail visibility, case report export, case
list management, dashboard metrics, and authentication.

See the canonical text in [docs/architecture/01-user-stories.md](../../../docs/architecture/01-user-stories.md).
Index: [user-stories-index](user-stories-index.md).

---

## US-026 — View Full Audit Trail

**Actor:** Judge

> As a judicial officer, I want to view a timestamped log of all agent
> actions, inputs, outputs, and tool calls, so that I can verify the
> provenance and transparency of the AI analysis.

Filterable by agent, time, and action type. Includes Solace message IDs.
Immutable. JSON export for compliance.

## US-027 — Export Case Report

**Actor:** Judge

> As a judicial officer, I want to download a formatted case report in PDF
> or JSON format, so that I have a portable record of the AI analysis for
> filing, archival, or reference purposes.

PDF (human) and JSON (machine) formats. Every page marked
**"AI-Generated Decision Support — Not Official Judgment"**.

## US-028 — Search and Filter Cases

**Actor:** Judge

> As a judicial officer, I want to search and filter my cases by domain,
> status, date range, complexity, and outcome, so that I can efficiently
> manage my caseload and find specific cases.

Full-text search across summaries / parties / facts; combinable filters;
session-preserved state; pagination.

## US-029 — View Dashboard Overview

**Actor:** Judge

> As a judicial officer, I want to see aggregate metrics on cases
> processed, processing times, confidence distribution, escalation rates,
> and costs, so that I can understand system performance and my caseload
> patterns.

Per-domain case counts, processing time, confidence distribution,
escalation rate, cost-per-case. Time-window filters and drill-down.

## US-030 — Manage Session and Authentication

**Actor:** Judge

> As a judicial officer, I want to securely log in, maintain my session,
> and log out with token invalidation, so that case data is protected and
> only accessible to authenticated judicial officers.

JWT in HTTP-only secure cookies; session timeout with extend warning;
logout with server-side token invalidation; rate-limited login. **P1
security gap** — server-side session-table revocation check is currently
on the backlog (cf. VER-40).
