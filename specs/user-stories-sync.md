# Feature: User Stories — Cross-Repo Sync (Wiki + Linear + Backend + Frontend)

**Author:** Douglas Sim
**Date:** 2026-04-09
**Status:** Approved (decisions logged 2026-04-09)

## Decisions

- **Q1 (project creation):** Use a one-shot `curl` against the Linear
  GraphQL API for `projectCreate`. Do not extend `lsdlc-linear` for this
  pass.
- **Q2 (split vs dual-label):** **Split hybrids.** All existing issues are
  in Backlog with no linked PRs; churn cost is zero, and "one scope per
  label" was the explicit requirement.
- **Q3 (senior judge persona):** Add **US-040 Senior Judge — Review
  Referred Cases** to the next-steps queue (not in this round; defer
  drafting and ticket creation to a follow-up so this round stays scoped).
- **Q4 (project document size):** Confirmed — project Document holds the
  index only; full bodies live in the wiki.

---

## Problem

VerdictCouncil has a comprehensive user-story document at
`docs/architecture/01-user-stories.md` (30 stories, US-001..US-030), but:

- The repo wiki (`.linear-sdlc/wiki/`) is empty — no searchable, page-level
  representation of user stories exists for future Claude / linear-sdlc
  workflows.
- Linear has 29 active backlog issues (VER-7..VER-49 with gaps) plus a junk
  Canceled issue (VER-5). Some issues map cleanly to a US-xxx story, some are
  hybrids (endpoint + UI in one ticket), some are infra-only (SAM/Solace), and
  one is a bug (VER-42). There is no `frontend`/`backend` label, no `epic`/
  `sub-issue` hierarchy, and no Linear project to roll work up under.
- The user-story doc itself has gaps: no admin/operator persona, no
  in-hearing or post-decision flows, no amendment trail story. The user wants
  these added.
- Work spans **two repos** (backend at `VerdictCouncil_Backend`, frontend at
  `VerdictCouncil_Frontend`) and Linear has no signal of which side a given
  ticket belongs to.

If we don't fix this, the team has no single navigable index of user stories,
no Linear rollup of cross-repo work, and no mechanism for the wiki-driven
linear-sdlc workflows (`/next`, `/implement`) to retrieve story context.

## Solution

Three deliverables, executed in order:

1. **Extend the canonical user-stories doc** with 7 new stories — add
   sections 1.8 (Administration & Operations) and 1.9 (Hearing &
   Post-Decision) to `docs/architecture/01-user-stories.md`.

2. **Populate the wiki** with one index + grouped pages by section, derived
   from the canonical doc:
   - `concepts/user-stories-index.md` — table of all 37 stories with section,
     persona, FE/BE scope, and matching epic ID.
   - `concepts/user-stories-1-intake.md` (US-001..005)
   - `concepts/user-stories-2-evidence.md` (US-006..010)
   - `concepts/user-stories-3-witnesses.md` (US-011..013)
   - `concepts/user-stories-4-legal-research.md` (US-014..017)
   - `concepts/user-stories-5-arguments-deliberation.md` (US-018..021)
   - `concepts/user-stories-6-verdict-governance.md` (US-022..025)
   - `concepts/user-stories-7-audit-export-session.md` (US-026..030)
   - `concepts/user-stories-8-administration.md` (US-031..034) **new**
   - `concepts/user-stories-9-hearing-post-decision.md` (US-035..037) **new**

3. **Reconcile Linear** by:
   - Creating a new Linear **Project** "VerdictCouncil MVP" in the VER team.
   - Creating a project **Document** "User Stories" that mirrors the wiki
     index (will be re-synced on changes).
   - Creating one **Epic** issue per US-xxx story (37 epics) with labels
     `epic` + `frontend`/`backend` (or both).
   - Re-parenting existing VER issues as **sub-issues** under the matching
     epic, splitting hybrid (FE+BE) issues into two cleanly-scoped sub-issues
     where needed. Each sub-issue is labeled `sub-issue` + exactly one of
     `frontend` or `backend`.
   - Cancelling VER-5 (junk).
   - Creating sub-issues for the gaps (US-005, US-024 backend, all of
     US-031..037).
   - Filing VER-42 (verdict ordering bug) as a sub-issue under the US-022
     epic with a `bug` label.
   - Filing VER-45..49 (SAM/Solace mesh) under a new "Phase 5: SAM Mesh"
     parent epic that is **not** US-aligned (orthogonal infra track).

## User Stories

This spec is itself about user stories — the delivered artifact is the
extended user-story set (US-001..US-037). The 7 new ones are drafted in full
in the **New User Stories** section below.

## Technical Approach

### Architecture

No application code changes. The work is documentation, wiki content, and
Linear hygiene. Tools used:

- `Write`/`Edit` for the canonical doc and wiki pages
- `lsdlc-linear` CLI for Linear mutations:
  - `create-issue --parent VER-xx --labels frontend,sub-issue` (for sub-issues)
  - `create-issue --labels epic,frontend,backend` (for epics)
  - `set-status VER-5 Canceled`
  - `add-relation` for explicit blocks/blockedBy
- A new helper invocation for project + project document creation. The
  `lsdlc-linear` tool exposes `list-projects` and `document-upsert` but does
  not currently expose `create-project` directly. **Open question** — see
  below.

### Data Model

No application data model changes. Linear data model additions:

- 1 new Linear Project ("VerdictCouncil MVP")
- 1 new Linear Project Document ("User Stories")
- 37 new Epic issues (one per US-xxx)
- 1 new orthogonal "Phase 5: SAM Mesh" parent epic
- ~10 new sub-issues to fill US gaps and split hybrids
- 4 new Linear labels: `epic`, `sub-issue`, `frontend`, `backend`
  (plus `bug` if not already present)

### API Changes

None.

### Dependencies

- Linear API key with permission to create projects, issues, labels, and
  documents (already configured — `lsdlc-linear whoami` returns Douglas Sim).
- The `lsdlc-linear` deprecated `search-issues` GraphQL field needs a
  workaround; will use `get-issue VER-N` probing for verification, which
  works.

## New User Stories (Drafted)

The following are the 7 new stories. They will be appended to
`docs/architecture/01-user-stories.md` under two new sections (1.8, 1.9).

---

### 1.8 Administration & Operations

#### US-031: Refresh / Re-index Vector Stores

**Actor:** Platform Administrator

As a platform administrator, I want to trigger a refresh of the SCT and
Traffic vector stores, so that the legal knowledge used by Agent 6 stays
current as statutes, judgments, and practice directions are added or amended.

**Acceptance Criteria:**
- Admin page lists vector stores (`vs_sct`, `vs_traffic`) with current
  document count and last-refresh timestamp in SGT
- Admin can trigger refresh per store or for all stores
- Refresh runs as a background job; admin sees progress
  (pending → fetching → embedding → indexing → complete/failed)
- Documents added or removed are reconciled and a diff report is shown
  post-refresh
- Successful refresh updates the last-refresh timestamp and document count
  visible to judges via US-017
- Failed refreshes surface a specific error and keep the previous index
  intact (no partial state)
- All refreshes are written to the audit trail with admin user ID and
  trigger reason (manual or scheduled)

**Happy Flow:**
1. Admin navigates to Settings → Knowledge Base.
2. Admin sees: "vs_sct — 342 docs — Last refresh: 15 Mar 2026 10:02 SGT —
   Status: Healthy".
3. Admin clicks "Refresh vs_sct" and enters a reason ("Consumer Protection
   Act amendment effective 1 April 2026").
4. System enqueues the refresh job; UI shows a progress indicator.
5. Admin watches the progress: "Fetching (42/50)... Embedding... Indexing...
   Complete — 12 added, 3 removed, 335 unchanged."
6. New document count (354) and timestamp are visible to all judges on the
   knowledge base status view (US-017).
7. Refresh action is recorded in the audit trail.

---

#### US-032: Monitor Agent & Pipeline Health

**Actor:** Platform Administrator / Ops Engineer

As an ops engineer, I want to see real-time health metrics for each of the
9 agents and the message bus, so that I can detect degradation early and
respond before it affects judges.

**Acceptance Criteria:**
- Dashboard displays per-agent metrics: last invocation time, invocations
  in 5min/1hr/24hr windows, success rate, p50/p95 latency, failure count
- Message bus health: Solace broker connectivity, queue depth per agent,
  dead-letter queue count
- Pipeline stage distribution histogram: how many cases are currently at
  each agent
- Alerts surface for: agent error rate above threshold, queue backlog,
  dead-letter accumulation
- Admin can click an agent tile to see recent failures with stack traces
  or error messages
- Historical view for the last 7 days with hourly buckets
- Health view is gated by the admin role (US-033)

**Happy Flow:**
1. Admin opens Ops → Pipeline Health.
2. System shows a 9-agent grid; each tile is green (healthy), amber
   (degraded), or red (failing).
3. Agent 6 (Legal Knowledge) shows amber: success rate 94% over the last
   hour.
4. Admin clicks Agent 6 and sees the recent failures — all are
   "PAIR API timeout after 15s".
5. Admin cross-references the Solace queue for Agent 6 and confirms there
   is no backlog.
6. Admin checks the US-039 / VER-49 total-precedent-source failure flag
   and confirms the flag is set correctly.
7. Admin escalates to the networking team about PAIR API latency and
   continues monitoring.

---

#### US-033: Manage User Accounts and Roles

**Actor:** Platform Administrator

As an administrator, I want to create, update, deactivate, and assign roles
to user accounts, so that only authorized judicial officers and staff can
access the system and their access matches their responsibilities.

**Acceptance Criteria:**
- Admin can list all accounts with filter by role (judge, senior_judge,
  admin) and status (active, inactive)
- Admin can create a new account with name, email, role, and either an
  initial password or an SSO identity binding
- Admin can deactivate an account; deactivation is non-destructive and
  preserves the audit trail and historical case assignments
- Admin can trigger a password reset (sends email or generates a one-time
  reset link)
- Admin can assign or revoke roles; role changes are logged with the
  acting admin's user ID
- System enforces role-based route access — judges see only case views,
  senior judges additionally see the refer-to inbox (US-024 follow-up),
  admins additionally see Settings/Ops
- Failed password resets and role changes are written to the audit trail

**Happy Flow:**
1. Admin navigates to Settings → Users.
2. Admin clicks "New User" and enters name "J. Tan", email
   "j.tan@courts.gov.sg", role "judge".
3. System creates the account and sends an email with a one-time setup
   link.
4. Admin later sees J. Tan in the active list after first login.
5. Three months later the admin changes J. Tan's role from "judge" to
   "senior_judge".
6. The change is logged: "2026-07-01 — admin Douglas Sim — role change —
   J. Tan — judge → senior_judge".
7. On next login, J. Tan sees the senior judge case-referral inbox.

---

#### US-034: Configure Cost and Quota Alerts

**Actor:** Platform Administrator

As an administrator, I want to configure OpenAI API spend thresholds and
per-judge quotas, so that the organization stays within its budget and no
single user can exhaust capacity.

**Acceptance Criteria:**
- Admin can set monthly total spend cap and warning thresholds
  (e.g., warn at 70%, 90%)
- Admin can set per-user quotas (cases per month or dollars per month)
- System tracks spend in real time against the configured thresholds
- Warning emails dispatch to admin when thresholds are crossed
- Hard cap enforcement: new case submissions are blocked with a clear
  message when a quota is exhausted; admin can grant a one-time override
- Dashboard shows current spend, projected month-end spend, top spenders,
  and remaining quota per user
- All quota overrides are written to the audit trail

**Happy Flow:**
1. Admin opens Settings → Costs.
2. Admin sets monthly cap at $500, warning at 80%, per-judge cap at 100
   cases/month.
3. Mid-month, total spend hits 80%. Admin receives email: "VerdictCouncil
   spend at 80% ($400/$500) — 14 days remaining".
4. Admin reviews the dashboard; sees Judge J. Tan has exceeded the
   per-judge cap (110 / 100).
5. Admin grants a one-time override for J. Tan with reason "urgent backlog
   clearance".
6. The override is logged. J. Tan can continue to submit cases.
7. At month-end, admin reviews total spend: $478, within cap.

---

### 1.9 Hearing & Post-Decision

#### US-035: Take In-Hearing Notes

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to take notes during the hearing that are
anchored to specific items in the hearing pack, so that my contemporaneous
observations are captured alongside the AI analysis and can be incorporated
into the recorded decision.

**Acceptance Criteria:**
- Hearing pack (US-020) supports a note-taking mode that locks the pack
  content and enables per-item annotations
- Notes are free-text, automatically timestamped, and associated with the
  hearing pack item (fact, question, gap, etc.)
- Judge can also add general notes not tied to a specific pack item
- Notes can be marked "probative" (carried into decision reasoning) or
  "administrative" (scheduling, logistics)
- Notes are saved locally and synced to the server at intervals, so the
  flow is resilient to brief connectivity loss
- Notes become part of the case record and are visible in the audit trail
- Notes can be referenced when recording the judicial decision (US-025)
- Note-taking mode does not block pipeline re-processing if new documents
  are uploaded mid-hearing (US-005)

**Happy Flow:**
1. Judge opens the case during a hearing and clicks "Start Hearing Mode".
2. System locks the hearing pack content and enables the notes sidebar.
3. Judge hears the witness's testimony and annotates a disputed fact:
   "Witness now admits the defect was visible on delivery — contradicts
   written statement".
4. Judge marks this note "probative".
5. System auto-saves the note with timestamp 2026-04-15 10:32:14 SGT.
6. Judge adds a general administrative note: "Reconvened after 10min break
   at 10:45".
7. Hearing ends; judge clicks "End Hearing Mode" and the session locks the
   notes for immutability.
8. Judge records the decision (US-025); the "probative" notes are surfaced
   for reference in the reasoning field.

---

#### US-036: Amend a Recorded Decision

**Actor:** Tribunal Magistrate / Judge

As a judicial officer, I want to amend a previously recorded judicial
decision with a clear amendment trail, so that corrections or post-hearing
updates are reflected without erasing the original record.

**Acceptance Criteria:**
- A recorded decision can be amended at any time before the case is
  archived
- Amendment requires selecting an amendment type (`clerical_correction`,
  `post_hearing_update`, `error_correction`) and a written reason
- Original decision is preserved; an amendment creates a new decision
  record linked to its predecessor
- Audit trail shows the full chain: original → amendment 1 → amendment 2,
  each with timestamp and judge ID
- Amendments appear in the exported case report (US-027) as an appendix
- Only the recording judge or a senior judge can amend; amendment attempts
  by other users are rejected
- Notification to parties is **out of scope** for the MVP (placeholder for
  future email integration)

**Happy Flow:**
1. Judge opens a case where they previously recorded a "Modify" decision
   with damages of $3,500.
2. Judge realises there was a clerical error in the amount ($3,500 should
   have been $3,800).
3. Judge clicks "Amend Decision" on the Verdict tab.
4. System presents the amendment form: type "clerical_correction", reason
   "Typographical error in original entry; correct amount is $3,800 per
   hearing notes".
5. Judge confirms. System creates a new decision record linked to the
   original and updates the case status to "Decision Amended".
6. Judge opens the audit trail and sees the chain: "Original: 14 Apr 2026
   $3,500 — Amendment (clerical_correction): 15 Apr 2026 $3,800".
7. Judge exports the case report; amendments appear in the Decision
   section with the full history.

---

#### US-037: Reopen a Closed Case

**Actor:** Tribunal Magistrate / Judge (with senior judge approval)

As a judicial officer, I want to reopen a closed case with appropriate
justification and approval, so that new evidence, appeals, or clerical
errors can be addressed without losing the original case history.

**Acceptance Criteria:**
- Reopen is gated: only senior judges can approve reopening; a regular
  judge can only request it
- Reopen request requires reason (`new_evidence`, `appeal`,
  `clerical_error`, `procedural_defect`), written justification, and any
  attached documents
- When approved, the case transitions from "Closed" to "Reopened" and the
  full pipeline can be re-triggered if needed
- Original closure record, decision, and audit trail are preserved and
  visible alongside the reopened analysis
- Reopened cases are clearly flagged in the case list
- Reopening is logged in the audit trail with both the requesting judge
  and the approving senior judge
- Reopened cases can be re-closed following the normal flow

**Happy Flow:**
1. Judge is notified that a claimant has submitted new evidence on a case
   closed 3 weeks ago.
2. Judge navigates to the closed case and clicks "Request Reopen".
3. Judge enters reason "new_evidence", justification "Claimant has produced
   the original quotation document that was not available at the original
   hearing".
4. Judge attaches the new document and submits the request.
5. System routes the request to the senior judge inbox.
6. Senior judge reviews the justification and new evidence, clicks
   "Approve Reopen".
7. Case status changes to "Reopened", pipeline is re-triggered with the
   new document (US-005 flow), and the original judge is notified.
8. Original closure record remains visible in the case history.

---

## Linear Reconciliation Plan

### Project + Document

- Create Linear Project: **"VerdictCouncil MVP"** in team VER
- Create Project Document: **"User Stories"** containing the wiki
  `concepts/user-stories-index.md` content (re-synced on doc edits)
- All 37 epics + the Phase-5 SAM epic + the orthogonal hotfix bucket are
  attached to this project

### Labels (create if missing)

- `epic` — applies to parent stories
- `sub-issue` — applies to children
- `frontend` — UI/React work
- `backend` — Python/FastAPI/pipeline work
- `bug` — non-feature defects

### Per-existing-VER reconciliation

Status notation:
- **KEEP** — leave as is, just relabel and reparent
- **SPLIT** — split a hybrid into FE + BE sub-issues (the original is closed
  and replaced)
- **CANCEL** — close as junk
- **NEW** — create from scratch

| VER | US epic | Action | New scope/labels |
|---|---|---|---|
| 5  | — | CANCEL | already canceled, no-op |
| 7  | US-001 | KEEP | `backend`, `sub-issue` |
| 8  | US-001 | KEEP | `backend`, `sub-issue` |
| 9  | US-001 | KEEP | `backend`, `sub-issue` |
| 10 | US-002 | KEEP | `backend`, `sub-issue` |
| 11 | US-003 | KEEP | `backend`, `sub-issue` |
| 12 | US-004 | KEEP | `backend`, `sub-issue` |
| 14 | US-030 | KEEP | `frontend`, `sub-issue` |
| 15 | US-001 | KEEP | `frontend`, `sub-issue` |
| 16 | US-002 | KEEP | `frontend`, `sub-issue` |
| 17 | US-009 + US-010 | KEEP, attach to **US-009** epic, link to US-010 | `frontend`, `sub-issue` |
| 18 | US-016 | KEEP | `frontend`, `sub-issue` |
| 19 | US-017 | KEEP | `frontend`, `sub-issue` |
| 20 | US-023 | KEEP | `frontend`, `sub-issue` |
| 21 | US-024 | KEEP | `frontend`, `sub-issue` |
| 22 | US-025 | KEEP | `frontend`, `sub-issue` |
| 23 | US-026 | SPLIT into 23a `backend` audit-trail endpoint + 23b `frontend` filter UI + JSON export button | both `sub-issue` |
| 25 | US-006 | SPLIT 25a BE / 25b FE | both `sub-issue` |
| 26 | US-007 | SPLIT 26a BE / 26b FE | both `sub-issue` |
| 27 | US-008 | SPLIT 27a BE / 27b FE | both `sub-issue` |
| 28 | US-011 (+ link US-012) | SPLIT 28a BE / 28b FE | both `sub-issue` |
| 29 | US-013 | SPLIT 29a BE / 29b FE | both `sub-issue` |
| 30 | US-014 + US-015 | SPLIT 30a BE / 30b FE; attach to US-014 epic, link to US-015 | both `sub-issue` |
| 31 | US-018 | SPLIT 31a BE / 31b FE | both `sub-issue` |
| 32 | US-019 (+ link US-021) | SPLIT 32a BE / 32b FE | both `sub-issue` |
| 33 | US-022 | SPLIT 33a BE / 33b FE | both `sub-issue` |
| 35 | US-020 | SPLIT 35a BE / 35b FE | both `sub-issue` |
| 36 | US-027 | SPLIT 36a BE / 36b FE | both `sub-issue` |
| 37 | US-028 | SPLIT 37a BE / 37b FE | both `sub-issue` |
| 38 | US-029 | SPLIT 38a BE / 38b FE | both `sub-issue` |
| 40 | US-030 | KEEP | `backend`, `sub-issue` |
| 41 | US-030 | KEEP | `frontend`, `sub-issue` |
| 42 | US-022 | KEEP, reparent | `backend`, `sub-issue`, `bug` |
| 45 | Phase 5 SAM | KEEP, reparent | `backend`, `sub-issue` |
| 46 | Phase 5 SAM | KEEP, reparent | `backend`, `sub-issue` |
| 47 | Phase 5 SAM | KEEP, reparent | `backend`, `sub-issue` |
| 48 | Phase 5 SAM | KEEP, reparent | `backend`, `sub-issue` |
| 49 | Phase 5 SAM | KEEP, reparent | `backend`, `sub-issue` |

> **Note on SPLIT vs in-place:** The user requested "in-place update". For
> tickets that already have a single clear scope (FE or BE), we keep them in
> place. For hybrid "endpoint + UI" tickets, splitting is the only way to
> apply a single `frontend` or `backend` label per the requirement. The
> hybrid issue is closed as "Replaced by VER-Xa + VER-Xb" so PR/commit
> history still resolves the original ID.

### Gap-filler new sub-issues to create

- **US-005** — Re-upload / add documents to existing case
  - 5a `backend` document append + affected-stage detection + selective
    re-trigger
  - 5b `frontend` "Add Documents" button + version history list
- **US-024** — Escalation
  - already has VER-21 for FE; need a new BE sub-issue for the escalation
    endpoint and audit log entry
- **US-031..034** — admin (8 sub-issues, 4 BE + 4 FE)
- **US-035..037** — hearing/post-decision (6 sub-issues, 3 BE + 3 FE)

Estimated total new sub-issues to create: ~30 (including splits + gaps).

### Execution order

1. Create labels (`epic`, `sub-issue`, `frontend`, `backend`, `bug`)
2. Create Linear Project "VerdictCouncil MVP"
3. Append US-031..037 to `docs/architecture/01-user-stories.md` (sections
   1.8 and 1.9)
4. Generate the wiki pages from the canonical doc
5. Create 37 US-xxx epic issues + Phase-5 SAM epic
6. Reparent existing VER-* issues to their epics; relabel
7. Split hybrid issues (close original; create FE/BE pair)
8. Create gap-filler sub-issues (US-005, US-024 BE, US-031..037)
9. Cancel VER-5 (no-op, already canceled)
10. Upsert the project Document "User Stories"
11. Verify with `lsdlc-linear get-issue` probes that the parent links work

## Open Questions

- [ ] **`lsdlc-linear` create-project**: the helper currently exposes
  `list-projects` and `document-upsert` but the project-creation path is
  not in `--help`. Options: (a) extend `lsdlc-linear` with a `create-project`
  subcommand, (b) create the project once via the Linear web UI and pass
  its UUID to the script, (c) use a one-shot `curl` against the Linear
  GraphQL API with the same key.
- [ ] **Splitting policy**: confirm splitting hybrid VER tickets is OK
  (closes the original ID; PRs that already reference the old ID still
  resolve, but the work item moves). If you'd rather keep the hybrid IDs,
  the alternative is to apply both `frontend` and `backend` labels to one
  issue and accept a slightly fuzzier label scheme.
- [ ] **Senior judge persona for US-037**: the role gating for reopen
  approval introduces a new persona implicit in US-024 ("refer to senior
  judge"). Should we add a US-040 "Senior Judge — Review Referred Cases"
  story now, or is that a Phase-2 follow-up?
- [ ] **Project document size**: the User Stories index is small (~37
  rows), but if we ever want full story bodies in the project document,
  Linear has a 64KB document limit. Plan: keep the project document as the
  index only; the per-section pages live in the wiki.

## Acceptance Criteria

- [ ] `docs/architecture/01-user-stories.md` contains US-001..US-037 with
      sections 1.1..1.9
- [ ] `.linear-sdlc/wiki/concepts/user-stories-*.md` exists for all 9
      sections plus the index
- [ ] `lsdlc-wiki search "user story"` returns matches
- [ ] Linear Project "VerdictCouncil MVP" exists in team VER
- [ ] Linear has 37 epic issues, one per US-xxx, each with the `epic`
      label and at least one of `frontend`/`backend`
- [ ] Every existing VER-* (active) issue is parented to a US-xxx epic
      (or to the Phase-5 SAM epic) and labeled `sub-issue` plus exactly
      one of `frontend`/`backend`
- [ ] No US-xxx is left without at least one sub-issue covering its
      acceptance criteria
- [ ] The project Document "User Stories" is created and linked from the
      project page
- [ ] `tasks/lessons.md` is updated with any patterns surfaced during
      execution (per CLAUDE.md self-improvement loop)

## Out of Scope

- Implementing any of US-031..037 — this spec only **drafts** them.
- Adding new senior-judge or admin authentication flows beyond what
  US-030 already covers.
- Cross-repo CI changes or branch protection rules.
- Updating the Frontend repo's `tasks/todo.md` or README — these will get
  refreshed naturally during US-031..037 implementation later.
- Deleting or rewriting historical Linear issue comments / commits.
- Any work on the SAM/Solace mesh itself (VER-45..49) — only their
  parenting is touched.
