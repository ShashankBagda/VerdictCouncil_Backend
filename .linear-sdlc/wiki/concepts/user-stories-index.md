---
updated: 2026-04-09T18:00:00Z
sources:
  - ../../../docs/architecture/01-user-stories.md
  - ../../../specs/user-stories-sync.md
---
# User Stories Index

The VerdictCouncil judicial decision-support product is specified by **38 user
stories** (US-001..US-037 and US-040; US-038 and US-039 are reserved) grouped
into 10 thematic sections. The canonical text lives in
[docs/architecture/01-user-stories.md](../../../docs/architecture/01-user-stories.md).
This wiki index summarizes them for fast lookup; for full acceptance criteria
and happy flows, follow the link to the canonical doc or to the per-section
wiki page.

## Personas

- **Judge / Tribunal Magistrate** — primary user. Owns case acceptance,
  evidence review, deliberation, and final decision (US-001..030, 035..037).
- **Senior Judge** — reviews referred / escalated cases, approves reopens,
  and reviews amendments-of-record via the unified inbox (US-040).
  Touchpoints in US-024, US-036, US-037.
- **Platform Administrator / Ops Engineer** — manages knowledge base,
  monitors agent health, manages users, configures cost (US-031..034).

## Sections

| § | Title | Stories | Wiki page |
|---|---|---|---|
| 1.1 | Case Intake & Setup | US-001..005 | [intake](user-stories-1-intake.md) |
| 1.2 | Evidence & Facts | US-006..010 | [evidence](user-stories-2-evidence.md) |
| 1.3 | Witness Analysis | US-011..013 | [witnesses](user-stories-3-witnesses.md) |
| 1.4 | Legal Research | US-014..017 | [legal-research](user-stories-4-legal-research.md) |
| 1.5 | Arguments & Deliberation | US-018..021 | [arguments-deliberation](user-stories-5-arguments-deliberation.md) |
| 1.6 | Verdict & Governance | US-022..025 | [verdict-governance](user-stories-6-verdict-governance.md) |
| 1.7 | Audit, Export & Session | US-026..030 | [audit-export-session](user-stories-7-audit-export-session.md) |
| 1.8 | Administration & Operations | US-031..034 | [administration](user-stories-8-administration.md) |
| 1.9 | Hearing & Post-Decision | US-035..037 | [hearing-post-decision](user-stories-9-hearing-post-decision.md) |
| 1.10 | Senior Judge Operations | US-040 | [senior-judge](user-stories-10-senior-judge.md) |

## Full Roster

| ID | Title | Persona | FE/BE | Linear epic |
|---|---|---|---|---|
| US-001 | Upload New Case | Judge | FE+BE | tbd |
| US-002 | View Document Processing Status | Judge | FE+BE | tbd |
| US-003 | Receive Jurisdiction Validation Result | Judge | FE+BE | tbd |
| US-004 | Handle Rejected Cases | Judge | FE+BE | tbd |
| US-005 | Re-upload or Add Documents to Existing Case | Judge | FE+BE | tbd (gap) |
| US-006 | Review Evidence Analysis Dashboard | Judge | FE+BE | tbd |
| US-007 | View Fact Timeline | Judge | FE+BE | tbd |
| US-008 | Drill Down to Source Document | Judge | FE+BE | tbd |
| US-009 | Flag Disputed Facts | Judge | FE | tbd |
| US-010 | Review Evidence Gaps | Judge | FE | tbd |
| US-011 | Review Witness Profiles and Credibility Scores | Judge | FE+BE | tbd |
| US-012 | View Anticipated Testimony (Traffic Only) | Judge | FE+BE | tbd |
| US-013 | Review Suggested Judicial Questions | Judge | FE+BE | tbd |
| US-014 | Review Applicable Statutes | Judge | FE+BE | tbd |
| US-015 | Review Precedent Cases | Judge | FE+BE | tbd |
| US-016 | Search Live Precedent Database | Judge | FE | tbd |
| US-017 | View Knowledge Base Status | Judge | FE | tbd |
| US-018 | Review Both Sides' Arguments | Judge | FE+BE | tbd |
| US-019 | Review Deliberation Reasoning Chain | Judge | FE+BE | tbd |
| US-020 | Prepare Hearing Pack | Judge | FE+BE | tbd |
| US-021 | Compare Alternative Outcomes | Judge | FE+BE | tbd |
| US-022 | Review Verdict Recommendation | Judge | FE+BE | tbd |
| US-023 | Review Fairness and Bias Audit | Judge | FE | tbd |
| US-024 | Handle Escalated Cases | Judge | FE+BE | tbd (BE gap) |
| US-025 | Record Judicial Decision | Judge | FE | tbd |
| US-026 | View Full Audit Trail | Judge | FE+BE | tbd |
| US-027 | Export Case Report | Judge | FE+BE | tbd |
| US-028 | Search and Filter Cases | Judge | FE+BE | tbd |
| US-029 | View Dashboard Overview | Judge | FE+BE | tbd |
| US-030 | Manage Session and Authentication | Judge | FE+BE | tbd |
| US-031 | Refresh / Re-index Vector Stores | Admin | FE+BE | tbd (new) |
| US-032 | Monitor Agent & Pipeline Health | Ops | FE+BE | tbd (new) |
| US-033 | Manage User Accounts and Roles | Admin | FE+BE | tbd (new) |
| US-034 | Configure Cost and Quota Alerts | Admin | FE+BE | tbd (new) |
| US-035 | Take In-Hearing Notes | Judge | FE+BE | tbd (new) |
| US-036 | Amend a Recorded Decision | Judge | FE+BE | tbd (new) |
| US-037 | Reopen a Closed Case | Judge + Senior Judge | FE+BE | tbd (new) |
| US-040 | Senior Judge — Review Referred Cases | Senior Judge | FE+BE | VER-133 (FE: VER-135 · BE: VER-134) |

> **`tbd` placeholders** are filled in by the Linear reconciliation pass
> (see [specs/user-stories-sync.md](../../../specs/user-stories-sync.md)).
> After the pass, this index is re-synced to the Linear Project Document
> "User Stories" under the **VerdictCouncil MVP** project.
