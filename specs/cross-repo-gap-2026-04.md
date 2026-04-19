# Feature: VerdictCouncil Cross-Repo Gap Closure (US-001 → US-030)

**Author:** douglasswm
**Date:** 2026-04-09
**Status:** Draft

---

## Problem

A line-by-line audit of the 30 user stories in `docs/architecture/01-user-stories.md` against both repos
(`VerdictCouncil_Backend` and `VerdictCouncil_Frontend`) revealed that the product cannot be exercised
end-to-end by a judge today.

**Headline numbers**

| | Backend | Frontend |
|---|---|---|
| ✅ Done | 8 | 1 |
| 🟡 Partial | 19 | 7 |
| ❌ Not started | 3 | 22 |

**Root causes**

1. **The keystone story (US-001 Upload New Case) is broken across the seam.** `POST /api/v1/cases/`
   creates a row but does not accept files, does not validate domain-specific fields (SCT claim amount,
   Traffic offence code), and never enqueues `PipelineRunner.run()`. The frontend intake form is rich
   but writes only to `localStorage` — `backendAdapter.js` contains zero `fetch` calls.
2. **The frontend is 100% mocked.** Agent outputs in `App.jsx` `buildAgentDraft()` (lines 53–135) are
   synthesised client-side; pipeline progression is a client-side timer (`App.jsx:502-508`); audit
   trails live in React state. None of the backend endpoints are reachable from the UI.
3. **Group B judge endpoints shipped in v0.1.0.0 are stranded.** Eight backend endpoints (US-009, 010,
   016, 017, 023, 024, 025, 026) work but have no UI consumer. Backend has been racing ahead of
   frontend integration.
4. **Pipeline runs only as the in-process prototype.** `PipelineRunner` exists; the SAM/Solace mesh
   does not. K8s manifests and SAM tools are present but not wired. Phase 5 in `TODOS.md`.
5. **Known P1 security gap.** `get_current_user` (`deps.py`) decodes JWTs but never checks the
   `sessions` table — a revoked judge keeps API access until JWT expiry.

If we don't address these, the v0.1.0.0 release demo path is "look at JSON in Postman" rather than
"a judge uploads a case and reviews the analysis."

## Solution

Group the 30-story gap into **six epics**, each scoped so it can ship independently and produce a
visible vertical slice. Tickets sequence so that Epic 1 (Intake Pipeline) unblocks the hot path; Epic
2 (FE Integration) lights up the work that already exists; Epics 3–6 fill in the remainder.

Recommended sequence: **E1 → E2 (parallel with E3) → E4 → E5 → E6**.

## User Stories

This spec consolidates all 30 user stories from `docs/architecture/01-user-stories.md`. Each child
ticket below is tagged with the US-### identifier(s) it closes.

---

## Epic 1 — Intake Pipeline End-to-End

**Goal:** A judge can upload documents, the case is validated, and the 9-agent pipeline runs.

**Stories closed:** US-001, US-002, US-003, US-004, US-005

### Tickets

#### E1-T1 — Document upload endpoint with OpenAI Files API
- **US:** US-001, US-005
- **What:** Add `POST /api/v1/cases/{case_id}/documents` accepting multipart upload of PDF/JPEG/PNG/text.
  Stream each file to the OpenAI Files API, persist `Document` rows with `openai_file_id`. For US-005,
  allow appending documents to non-closed cases and identify which pipeline stages must re-run.
- **Acceptance:**
  - Multipart upload accepts up to N files (configurable cap)
  - File-type and size validation rejects with structured error
  - Returns `{document_id, openai_file_id, filename, uploaded_at}` per file
  - Re-upload to existing case appends, does not replace; version history queryable

#### E1-T2 — Domain-specific case validation on creation
- **US:** US-001
- **What:** Extend `CaseCreateRequest` to require `claim_amount` for SCT (validate ≤ $20k, or ≤ $30k
  with consent flag) and `offence_code` for Traffic (validate against maintained code list). Reject
  with 422 + structured field errors.
- **Acceptance:**
  - SCT case without claim_amount returns 422
  - Traffic case with unknown offence_code returns 422 referencing the rejected code
  - Valid cases persist the new fields and pass them to Agent 1

#### E1-T3 — Trigger PipelineRunner from case creation
- **US:** US-001, US-002
- **What:** After successful document upload, transition case status to `processing` and enqueue
  `PipelineRunner.run()` as a background task. Persist run state so polling works.
- **Acceptance:**
  - Case status moves to `processing` synchronously on first document upload
  - Pipeline run is non-blocking from the API caller's perspective
  - Run state survives server restart (persisted in DB or Redis)

#### E1-T4 — Pipeline status endpoint (polling phase)
- **US:** US-002
- **What:** Add `GET /api/v1/cases/{case_id}/status` returning per-agent state, current agent,
  elapsed time per stage, and overall elapsed time. Polling-based MVP; SSE deferred to Epic 6.
- **Acceptance:**
  - Returns 9-element array of `{agent_id, name, state, started_at, finished_at, error?}`
  - State enum: `pending | in_progress | completed | failed`
  - Failed stages include error summary

#### E1-T5 — Jurisdiction validation result endpoint
- **US:** US-003
- **What:** Add `GET /api/v1/cases/{case_id}/jurisdiction` exposing Agent 1's
  `Case.jurisdiction_valid` plus the underlying check breakdown (claim_amount vs cap, filing date vs
  limitation period, offence code validity).
- **Acceptance:**
  - Returns `pass | fail | warning` plus per-criterion detail
  - Borderline cases flagged as `warning` not `fail`
  - Fail responses cite the specific statutory threshold

#### E1-T6 — Rejection override workflow
- **US:** US-004
- **What:** Add `POST /api/v1/cases/{case_id}/override-rejection` with required justification text.
  Logs to audit trail, transitions case back to `processing`, resumes pipeline from the rejected
  stage. Distinct from the existing `escalated-cases reject` action.
- **Acceptance:**
  - Justification text required, sanitized
  - Audit log entry recorded with judge user_id and timestamp
  - Pipeline resumes from the stage that rejected the case
  - Closed cases cannot be overridden (returns 409)

---

## Epic 2 — Frontend API Integration & Stranded UI

**Goal:** Replace the 100% mocked frontend with real API calls. Light up the 8 stranded backend
endpoints. Wire the pipeline visualization to real status data.

**Stories closed (UI side):** US-001, US-002, US-009, US-010, US-016, US-017, US-023, US-024,
US-025 (Modify), US-026

### Tickets

#### E2-T1 — Backend API client + auth interceptor
- **What:** Replace `backendAdapter.js` localStorage logic with a real API client (fetch wrapper)
  that includes credentials, handles 401 → redirect to login, and surfaces structured errors.
  Environment-driven base URL.
- **Acceptance:**
  - All API calls go through one client module
  - 401 globally redirects to login
  - Errors propagate as typed objects, not silent failures

#### E2-T2 — Wire intake form to real upload + create
- **US:** US-001
- **What:** `AppealIntakePage.jsx` submission calls `POST /cases/`, then streams files to
  `POST /cases/{id}/documents`, then transitions to the pipeline status view. Display per-file
  upload progress.
- **Acceptance:**
  - Successful submission produces a real case_id from the backend
  - Upload errors shown inline per file
  - Demo case scenario hits a feature flag, not the real upload path

#### E2-T3 — Replace mocked pipeline with real status polling
- **US:** US-002
- **What:** Remove client-side timer in `App.jsx:502-508`. Poll `GET /cases/{id}/status` at a
  configurable interval. Render real per-agent state in `AgentPipelinePage.jsx` and
  `GraphMeshPage.jsx`. Stop polling on terminal state.
- **Acceptance:**
  - No agent draft is generated client-side
  - Polling stops when all agents reach terminal state or page unmounts
  - Failed agents show retry affordance (calls existing escalation actions)

#### E2-T4 — Evidence Gaps + Disputed Facts UI
- **US:** US-009, US-010
- **What:** New `EvidenceGapsPage` consuming `GET /cases/{id}/evidence-gaps`. New disputed-facts
  view calling `PATCH /cases/{id}/facts/{fact_id}/dispute` with reason input.
- **Acceptance:**
  - Gap list grouped by burden party
  - Dispute modal posts reason and updates fact in place
  - 409 on already-disputed fact handled gracefully

#### E2-T5 — Live precedent search panel
- **US:** US-016
- **What:** Search panel UI calling `POST /api/v1/precedents/search` with custom keywords. Display
  results tagged `live_search` alongside curated results. Show last-search timestamp.
- **Acceptance:**
  - Results tagged by source
  - PAIR circuit-breaker fallback surfaced as a banner ("results from vector store")
  - eLitigation links open in new tab

#### E2-T6 — Knowledge base status indicator
- **US:** US-017
- **What:** Header chip + dedicated panel calling `GET /api/v1/knowledge-base/status`. Render
  vector store doc count, last update, health.
- **Acceptance:**
  - Stale store shows warning chip on case analysis view
  - Status panel reachable from global nav

#### E2-T7 — Fairness audit checklist UI
- **US:** US-023
- **What:** New tab on case analysis view consuming `GET /cases/{id}/fairness-audit`. Render as a
  checklist of governance checks with pass/warning/fail icons; click to drill into rationale.
- **Acceptance:**
  - Critical (fail) items pinned to top
  - Acknowledge button records judge action via existing audit endpoint

#### E2-T8 — Escalated cases inbox + actions UI
- **US:** US-024
- **What:** Inbox view calling `GET /escalated-cases/`. Per-case detail with the 4 existing actions
  (`add_notes`, `return_to_pipeline`, `manual_decision`, `reject`).
- **Acceptance:**
  - Pagination wired to backend
  - `manual_decision` form captures verdict + reason
  - Action results refresh the inbox

#### E2-T9 — Modify decision form
- **US:** US-025
- **What:** Extend the existing approve/redirect controls in `GraphMeshPage.jsx` with a
  three-option form (Accept / Modify / Reject). Modify and Reject require reason text. Submit to
  `POST /cases/{id}/decision`.
- **Acceptance:**
  - All three decision types reach the backend
  - Reason text required for Modify and Reject
  - Once recorded, controls disable (no edit in place)

#### E2-T10 — Filterable audit trail with JSON export
- **US:** US-026
- **What:** Replace in-memory audit log with `GET /api/v1/audit/{case_id}/audit`. Add agent + time
  range filters and a JSON export button.
- **Acceptance:**
  - Filters round-trip to backend query params
  - Expand-row reveals full input/output payload
  - Export downloads the filtered slice

---

## Epic 3 — Aggregated Read Endpoints (Backend) + UI

**Goal:** Promote data that currently lives only in the generic `GET /cases/{id}` payload into
purpose-built endpoints, and build the matching frontend views.

**Stories closed:** US-006, US-007, US-008, US-011, US-012, US-013, US-014, US-015, US-018, US-019,
US-021, US-022

### Tickets

#### E3-T1 — Evidence dashboard endpoint + UI
- **US:** US-006
- **What:** `GET /api/v1/cases/{case_id}/evidence` returning per-item strength, admissibility flags,
  contradictions, corroborations, gap references, source citations. Frontend dashboard with
  sort/filter and contradiction expand-row.
- **Acceptance:**
  - Returns counts summary (strong/moderate/weak/inadmissible)
  - Filter by type, strength, flag
  - Drill into source via E3-T3

#### E3-T2 — Fact timeline endpoint + UI
- **US:** US-007
- **What:** `GET /api/v1/cases/{case_id}/timeline` returning ordered facts with date,
  description, source citations, confidence, and agreed/disputed/unilateral classification.
  Frontend timeline visualization with hover detail.
- **Acceptance:**
  - Disputed facts visually distinct, link to dispute view
  - Hover reveals confidence basis
  - Empty timeline returns 200 with empty array

#### E3-T3 — Source document drill-down endpoint + viewer
- **US:** US-008
- **What:** `GET /api/v1/documents/{document_id}` returning document metadata + signed retrieval
  URL or proxy stream. Frontend split-pane viewer that highlights the cited passage (text PDFs)
  or region (images).
- **Acceptance:**
  - Returns filename, page count, upload timestamp
  - Citations include page number / region coordinates
  - Viewer falls back gracefully when highlighting unavailable

#### E3-T4 — Witnesses endpoint + profiles UI
- **US:** US-011, US-012
- **What:** `GET /api/v1/cases/{case_id}/witnesses` returning per-witness profile, bias indicators,
  credibility breakdown, and (for Traffic) `simulated_testimony`. Frontend profile cards + score
  breakdown drill-down. Anticipated testimony section gated to Traffic with prominent "Simulated"
  banner.
- **Acceptance:**
  - SCT cases do not return simulated_testimony
  - Traffic UI displays mandatory banner
  - Credibility factors expand to show evidence basis

#### E3-T5 — Suggested questions endpoint + editor UI
- **US:** US-013
- **What:** `GET /api/v1/cases/{case_id}/questions` to read; `PUT` or per-item PATCH to edit, add,
  delete, reorder. Frontend list with tag chips and inline edit. Edits do not trigger pipeline
  re-run.
- **Acceptance:**
  - Question type tags: factual_clarification | evidence_gap | credibility_probe | legal_interpretation
  - Reorder persists
  - Edits available to hearing pack (E4-T1)

#### E3-T6 — Statutes + precedents endpoints + UI
- **US:** US-014, US-015
- **What:** `GET /api/v1/cases/{case_id}/statutes` and `GET /api/v1/cases/{case_id}/precedents`
  returning rich legal-knowledge results. Frontend Legal Framework + Precedents tabs with
  expand/collapse, sort, mark-relevant/distinguished, flag-not-applicable.
- **Acceptance:**
  - Statutes show verbatim text + relevance score + application narrative
  - Precedents include distinguishing factors + source tag (curated/live_search)
  - Per-judge marks persist

#### E3-T7 — Arguments endpoint + UI
- **US:** US-018
- **What:** `GET /api/v1/cases/{case_id}/arguments` returning structured arguments per side with
  strengths, weaknesses, and evidence chain links. SCT returns balanced strength %; Traffic returns
  prosecution/defence with contested issues. Frontend tab with "Internal Analysis" banner.
- **Acceptance:**
  - SCT vs Traffic shape diverges per spec
  - Banner non-dismissible
  - Evidence chain links resolve to E3-T1 items

#### E3-T8 — Deliberation reasoning chain endpoint + UI
- **US:** US-019, US-021
- **What:** `GET /api/v1/cases/{case_id}/deliberation` returning numbered reasoning steps grouped
  into Factual / Legal / Conclusion, with per-step confidence and uncertainty factors. Same endpoint
  returns recommended + alternative outcomes for US-021. Frontend renders chain with low-confidence
  flags and a side-by-side outcome comparator.
- **Acceptance:**
  - Steps cite source agent + evidence
  - Low-confidence steps flagged amber/red
  - Alternative outcome shows pivot factors

#### E3-T9 — Verdict endpoint + UI
- **US:** US-022
- **What:** `GET /api/v1/cases/{case_id}/verdict` returning final recommendation + confidence + link
  to deliberation. Frontend Verdict tab with mandatory disclaimer banner.
- **Acceptance:**
  - SCT shape: order_type + amount; Traffic shape: verdict + sentence
  - Disclaimer non-dismissible
  - Links to Record Decision flow (US-025)

---

## Epic 4 — Export, Reporting, Search & Dashboard

**Goal:** Close out hearing-pack assembly, PDF/JSON export, advanced case search, and the metrics
dashboard.

**Stories closed:** US-020, US-027, US-028, US-029

### Tickets

#### E4-T1 — Hearing pack endpoint + UI
- **US:** US-020
- **What:** `POST /api/v1/cases/{case_id}/hearing-pack` consolidates outputs from multiple agents
  into a single document with sections: case overview, key facts, disputed issues, legal framework,
  suggested questions, strengths/weaknesses, evidence gaps. Frontend annotation UI + checklist
  conversion.
- **Acceptance:**
  - Annotations persist per judge per case
  - Custom items can be added
  - Reflects latest pipeline state (re-runs included)

#### E4-T2 — Case report export (PDF + JSON)
- **US:** US-027
- **What:** `GET /api/v1/cases/{case_id}/export?format=pdf|json`. PDF includes cover page, TOC,
  per-section content, audit trail appendix, and the disclaimer "AI-Generated Decision Support — Not
  Official Judgment" on every page. JSON returns the full structured data.
- **Acceptance:**
  - PDF formatted A4 with page numbers
  - Disclaimer on every page
  - JSON validates against a documented schema

#### E4-T3 — Advanced case search & filter
- **US:** US-028
- **What:** Extend `GET /api/v1/cases/` with date range, complexity, outcome filters and full-text
  search across summaries, party names, key facts. Frontend case list page with combinable filters,
  pagination, session-preserved state.
- **Acceptance:**
  - Combined filters round-trip via query params
  - Full-text search uses Postgres `tsvector` or equivalent
  - Pagination cursor stable across filter changes

#### E4-T4 — Dashboard overview endpoint + UI
- **US:** US-029
- **What:** Extend `GET /api/v1/dashboard/stats` with avg processing time, confidence distribution,
  escalation rate, cost per case (API usage), and per-window trend deltas. Frontend metric cards
  with up/down indicators and drill-down to underlying case lists.
- **Acceptance:**
  - Time windows: 7d, 30d, 90d, custom
  - Drill-down preserves the active filter
  - Loads within 3 seconds at expected case volumes

---

## Epic 5 — Auth Hardening & Frontend Login

**Goal:** Close the P1 session-revocation gap, add a frontend login flow, and address the ranked
technical debt items in `TODOS.md`.

**Stories closed:** US-030 (frontend); plus targeted technical debt items.

### Tickets

#### E5-T1 — Session-table revocation check (P1)
- **What:** `get_current_user` (`deps.py`) must validate the JWT against `Session.jwt_token_hash`
  and `expires_at` on every request. Revoked sessions return 401 immediately.
- **Acceptance:**
  - Logout invalidates the session row; subsequent requests with the same JWT 401
  - Expired sessions 401
  - Test coverage for revoked + expired paths
- **Priority:** **P1 — security gap.**

#### E5-T2 — Frontend login + session-extend warning
- **US:** US-030
- **What:** New login page calling `POST /auth/login`. Session-extend warning banner 5 minutes
  before expiry. Logout button calling `POST /auth/logout`.
- **Acceptance:**
  - Login persists JWT cookie automatically
  - 5-minute pre-expiry banner triggers
  - Logout clears cookie + redirects to login

#### E5-T3 — Verdict ordering by created_at
- **What:** Add `created_at` column to `Verdict` model + migration. Change `get_fairness_audit`
  ordering from `id.desc()` (random UUID) to `created_at.desc()`.
- **Acceptance:**
  - Migration applied; all existing rows backfilled
  - Most-recent verdict deterministic on re-runs

#### E5-T4 — Redis connection singleton in search_precedents
- **What:** Convert `_get_redis_client()` to a module-level singleton with proper lifecycle.
- **Acceptance:**
  - One Redis connection per process
  - No leaked connections under sustained load (verify with pool stats)

---

## Epic 6 — SAM/Solace Mesh Integration (Phase 5)

**Goal:** Migrate the in-process `PipelineRunner` to the SAM/Solace mesh, enabling SSE-based
real-time status, HA, and the K8s deploy path.

**Stories enhanced:** US-002 (real SSE), US-026 (Solace MsgID traceability).

### Tickets

#### E6-T1 — SAM agent definitions + Solace topic schema
- **What:** Define each of the 9 agents as a SAM component. Define request/response topic schema and
  CaseState contract. Document message flow.
- **Acceptance:**
  - SAM agents bootable locally
  - Topic schema versioned and documented
  - CaseState contract validates round-trip

#### E6-T2 — Mesh runner replacement
- **What:** Replace the in-process `PipelineRunner.run()` call with a Solace publish that the SAM
  mesh consumes. Maintain a runner-only fallback flag for local dev.
- **Acceptance:**
  - Production case creation enqueues via Solace
  - Local dev with `USE_RUNNER=true` uses prototype path
  - Runner-only fixes (per `TODOS.md`) ported to mesh path

#### E6-T3 — SSE pipeline status stream
- **What:** Replace polling endpoint from E1-T4 with SSE backed by Solace status messages. Frontend
  swaps poll for SSE.
- **Acceptance:**
  - SSE survives network blip with reconnect
  - Backend tracks last-event-id for resume
  - Polling endpoint kept as fallback

#### E6-T4 — Solace HA / managed broker
- **What:** Configure Solace HA pair or migrate to Solace Cloud managed service per `TODOS.md`.
- **Acceptance:**
  - Single broker failure does not halt pipeline
  - K8s manifests updated for HA topology

#### E6-T5 — Total precedent source failure flag (mesh path)
- **What:** Port the `precedent_unavailable` `SearchResult` metadata fix from runner to the SAM
  mesh path. Governance prompt updated.
- **Acceptance:**
  - When both PAIR and vector store return empty, fairness audit warns
  - Mesh-path metadata matches runner-path

---

## Technical Approach

### Architecture

- **No new services** in Epics 1–5. Epic 6 introduces the SAM mesh.
- All new endpoints follow existing FastAPI router conventions under
  `src/verdictcouncil/api/`.
- Frontend remains React + Vite + React Router; introduce a shared API client module under
  `src/lib/` and a typed error class.

### Data Model

- New columns: `Verdict.created_at` (E5-T3), optional `Document.version`/`Document.added_at`
  refinements for US-005.
- New tables: none for Epics 1–5. Epic 6 may add a `pipeline_run_state` table or Redis hash.
- Existing models already cover witnesses, arguments, verdict alternatives, deliberation steps —
  Epic 3 only adds endpoints, not schema.

### API Changes

- 20+ new endpoints across `cases`, `documents`, `judge`, `dashboard`, `audit`, `precedents` routers.
- All judge-facing endpoints require `judge` role.
- Document upload uses multipart; everything else JSON.

### Dependencies

- E1 unblocks the entire critical path. Nothing else has end-user value without it.
- E2 depends on E1-T2/T3/T4 for the pipeline-status work; the stranded-endpoint UI tickets in E2
  (T4–T10) are independent and can ship in parallel.
- E3 depends on E1 only insofar as the UI is meaningful once the pipeline runs.
- E4-T1 (hearing pack) depends on most of E3.
- E5 is independent and should be sequenced early because of the P1 security item.
- E6 depends on E1 + E2 stable.

## Open Questions

- [ ] Do we need authentication on E3-T3 document drill-down beyond the existing `judge` role check?
- [ ] PDF generation library: server-side (WeasyPrint, ReportLab) vs headless Chromium?
- [ ] Should hearing pack annotations be per-judge or per-case?
- [ ] Full-text search backend: Postgres `tsvector` or external (Meilisearch, Typesense)?
- [ ] Cost-per-case metric (US-029) — do we instrument OpenAI usage already, or is that net-new?
- [ ] Re-upload affected-stage detection (US-005, E1-T1) — heuristic or LLM-driven?

## Acceptance Criteria

- [ ] Epic 1 ships: a judge can upload a case, see real pipeline progress, and reach a verdict
- [ ] Epic 2 ships: frontend has zero hardcoded agent draft data; all 8 stranded BE endpoints have
      a UI consumer
- [ ] Epic 3 ships: every case-detail tab in the spec has its own endpoint and matching UI
- [ ] Epic 4 ships: hearing pack assembles, PDF + JSON export work, search and dashboard cover all
      acceptance criteria from US-027/028/029
- [ ] Epic 5 ships: P1 session-revocation gap closed; frontend login + logout work end-to-end
- [ ] Epic 6 ships: SAM mesh is the production pipeline path; SSE replaces polling; HA broker live

## Out of Scope

- Multi-tenancy / multi-court deployments
- Mobile / tablet-specific UI
- Localisation beyond English
- Real-time collaboration on the same case (multiple judges editing)
- Custom agent authoring by judges
- Anything in `TODOS.md → Future Scaling` (Redis sharding for Layer2Aggregator)
