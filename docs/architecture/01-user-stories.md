# Part 1: User Stories

> **Scope.** MVP for the assessment demo. Two personas: **Judge** (tribunal magistrate acting on cases) and **Admin** (court administrator managing the knowledge base, users, and system health). Stories tagged `MVP ✓` are fully implemented; `MVP (partial)` have backend support but acceptance bugs or limited UI; `Not in MVP` are captured here only for scope clarity. Each story cites the canonical entry point in code; the service / model layer is reachable from there.

---

## 1.1 Identity & Access

### US-030 — Sign in, stay in session, sign out

**As a** Judge or Admin, **I want** to sign in with email and password and stay signed in across page reloads, **so that** I can work across a case without re-authenticating.

- Email + password login; session persisted via `vc_token` httpOnly cookie (SameSite=Lax, Secure in prod).
- Session hash stored server-side so revoking a session invalidates the cookie everywhere.
- Logout clears the cookie and revokes the session row.
- Password reset via single-use token emailed to the user; token expires per `RESET_TOKEN_TTL_MINUTES`.
- Roles: `user`, `judge`, `senior_judge`, `admin`. Admin pages gated in the router.
- **Code:** `src/api/routes/auth.py` · `src/api/deps.py::get_current_user` · `src/models/user.py`
- **Status:** MVP ✓

---

## 1.2 Case Intake (Judge)

### US-001 — Upload a new case

**As a** Judge, **I want** to upload case documents (PDFs/images/text) with the case metadata, **so that** the pipeline can analyse it.

- Accepts PDF, JPEG, PNG, plain text up to `case_doc_max_upload_bytes` (default 50 MB/file).
- Files pushed to OpenAI Files API; `Document` row records `openai_file_id`, `kind`, `pages`.
- SCT submissions require claim amount within SCT jurisdiction; Traffic submissions require a known offence code.
- Case row enters `processing`; a `pipeline_jobs` row is written and a `run_case_pipeline_job` enqueued on arq.
- **Code:** `src/api/routes/cases.py::create_case` · `src/workers/tasks.py::run_case_pipeline_job`
- **Status:** MVP ✓

### US-002 — Watch the pipeline progress live

**As a** Judge, **I want** to see per-agent progress for a submitted case in real time, **so that** I know when it's ready to review.

- SSE stream from `GET /cases/{id}/events` emits agent-start / agent-complete / halt events.
- Frontend shows each agent (case-processing → … → hearing-governance) with status + elapsed time.
- Stream reconnects without data loss (consumer tracks last event id; events are checkpoint-backed).
- **Code:** `src/api/routes/cases.py::stream_pipeline_status` · `src/pipeline/graph/runner.py`
- **Status:** MVP ✓

### US-003 — See jurisdiction validation outcome

**As a** Judge, **I want** to see whether the case passes jurisdiction checks from Gate 1, **so that** I can confirm the tribunal can hear it before I invest review time.

- `case-processing` sets `case_metadata.jurisdiction_valid` + `jurisdiction_issues[]`.
- Judge-facing endpoint returns the Gate-1 jurisdiction block for the case.
- **Code:** `src/api/routes/judge.py::get_jurisdiction_validation`
- **Status:** MVP ✓

### US-005 — Add supplementary documents to an open case

**As a** Judge, **I want** to add or replace documents on a case that's still in review, **so that** I can correct missing exhibits without re-filing.

- Case must be in a non-terminal status (`processing`, `awaiting_review_gate*`, `ready_for_review`, `escalated`).
- New documents trigger a partial re-run from the affected gate using `start_agent`.
- **Code:** `src/api/routes/case_data.py::upload_supplementary_documents`
- **Status:** MVP ✓

### US-004 — Handle rejected cases (Not in MVP)

Explicit rejection flow with reason codes and re-submission guidance. Jurisdiction-fail cases are today surfaced via US-003 only. Parked.

---

## 1.3 Evidence & Facts (Judge)

### US-006 — Review the evidence dashboard

**As a** Judge, **I want** a single view of every exhibit with classification, strength, admissibility flags, and linked claims, plus a list of gaps flagged by `evidence-analysis`, **so that** I can identify weak evidence before the hearing.

- One row per evidence item with strength, admissibility flags, and source-document back-link.
- Dedicated "Gaps" panel listing missing evidence types flagged by the agent (absorbs US-010).
- Cross-references (corroborations/contradictions) link to both source segments.
- **Code:** `src/api/routes/judge.py::get_evidence_dashboard`
- **Status:** MVP ✓

### US-007 — View the fact timeline

**As a** Judge, **I want** an ordered timeline of extracted facts with agreed/disputed status and source anchors, **so that** I can see what happened and what's still contested.

- Chronological list across all facts with date/time, description, parties, source doc + page.
- Agreed vs disputed badge; conflicting versions expanded inline.
- **Code:** `src/api/routes/case_data.py::get_case_timeline`
- **Status:** MVP ✓

### US-008 — Drill from a fact/evidence entry to the source document

**As a** Judge, **I want** to jump from any fact or evidence item to the exact page of the source document, **so that** I can verify the AI's claim.

- Clickable anchors from `facts[].source_refs` / `evidence_items[].doc_refs` to a document-viewer endpoint that returns the parsed page excerpt (and an OpenAI file presigned URL where available).
- **Code:** `src/api/routes/documents.py::get_document_excerpt`
- **Status:** MVP ✓

### US-009 — Flag disputed facts for hearing focus

**As a** Judge, **I want** to mark a fact as "disputed" or "agreed" based on my own reading, **so that** the hearing reasoning reflects my tentative view.

- Toggles `facts[].status`; writes a judge-attributed `AuditEntry`.
- Dispute flips may drive a what-if re-run (see US-021) — the change does **not** mutate the original run; it creates a linked scenario.
- **Code:** `src/api/routes/judge.py::dispute_fact`
- **Status:** MVP ✓

---

## 1.4 Witnesses (Judge)

### US-011 — Review witness profiles and credibility

**As a** Judge, **I want** to see every identified witness with a credibility score and the dimensions that drove it, **so that** I can weigh their testimony.

- Per-witness view: role, party alignment, bias indicators, credibility score with breakdown (internal consistency, external consistency, bias, specificity, corroboration).
- Sort by credibility, filter by party.
- **Code:** `src/api/routes/case_data.py::get_case_witnesses`
- **Status:** MVP ✓

### US-013 — Review suggested judicial questions

**As a** Judge, **I want** a list of probe questions the system recommends asking each witness, **so that** I can adapt the hearing plan.

- Questions produced by the `generate_questions` tool during `witness-analysis`; editable and annotable by the judge before the hearing.
- **Code:** `src/api/routes/cases.py::update_suggested_questions` · `src/tools/generate_questions.py`
- **Status:** MVP ✓

### US-012 — Anticipated testimony view (Traffic only) (MVP partial)

Backend retains `Witness.simulated_testimony`, but only Traffic cases populate it and there's no judge-edit flow. Treat as read-only in demo.

---

## 1.5 Legal Knowledge (Judge)

### US-014 — Review applicable statutes

**As a** Judge, **I want** to see the statutes `legal-knowledge` considered applicable, with section text and relevance reasoning, **so that** I can verify the legal framework before I sit.

- Per-case statute list: `statute_ref`, `section`, verbatim text, relevance score, application-to-facts note.
- **Code:** `src/api/routes/case_data.py::get_case_statutes` · `src/tools/search_domain_guidance.py`
- **Status:** MVP ✓

### US-015 — Review precedents (curated + live)

**As a** Judge, **I want** to see precedents retrieved for this case, both from the curated KB and a live PAIR search, **so that** I can assess precedent fit.

- Per-case precedent list: citation, court, outcome, reasoning summary, similarity score, source (`curated` | `live_search`).
- Ad-hoc precedent search endpoint (absorbs US-016) for additional terms the judge wants to probe.
- Circuit-breaker status visible: if the PAIR API is open-circuit, results fall back to the curated vector store with a badge.
- **Code:** `src/api/routes/case_data.py::get_case_precedents` · `src/api/routes/precedent_search.py::search_precedents_adhoc`
- **Status:** MVP ✓

---

## 1.6 Arguments & Hearing Analysis (Judge)

### US-018 — Review both sides' arguments

**As a** Judge, **I want** to see the system's best construction of each side's argument, with strength bars and weaknesses, **so that** I can identify where submissions will need to land.

- Traffic cases: prosecution + defense arguments. SCT cases: claimant + respondent arguments.
- Each side: legal basis, supporting evidence refs, weaknesses, suggested judicial questions.
- **Code:** `src/api/routes/case_data.py::get_case_arguments`
- **Status:** MVP ✓

### US-019 — Review hearing analysis and reasoning (MVP partial)

**As a** Judge, **I want** to read the preliminary conclusion, reasoning chain, and uncertainty flags, **so that** I can scrutinise the chain of reasoning before convening the hearing.

- `HearingAnalysis` row exposes `preliminary_conclusion`, `confidence_score`, `reasoning_chain`, `uncertainty_flags`.
- Reasoning chain shows step-by-step: established facts → applicable law → application → argument evaluation.
- **Partial:** the richer fields (`established_facts`, `applicable_law`, `application`, `argument_evaluation`, `witness_impact`, `precedent_alignment`) are stored inside `reasoning_chain` as JSON; UI does not yet expand all of them cleanly.
- **Code:** `src/models/case.py::HearingAnalysis` (entry via `get_case_hearing_analysis` under `case_data.py`)
- **Status:** MVP (partial)

### US-021 — Run a what-if scenario

**As a** Judge, **I want** to toggle a fact, exclude evidence, tweak a witness's credibility, or change the legal interpretation and re-run the downstream pipeline, **so that** I can stress-test the conclusion.

- Four modification types: `fact_toggle`, `evidence_exclusion`, `witness_credibility`, `legal_interpretation`.
- Re-entry agent is determined by `WhatIfController.CHANGE_IMPACT_MAP`; original run is preserved via `parent_run_id`.
- Diff view (facts, arguments, preliminary conclusion, confidence, fairness check).
- Optional stability score asynchronously runs N perturbations.
- **Code:** `src/api/routes/what_if.py::submit_whatif_scenario` · `src/services/whatif_controller/controller.py`
- **Status:** MVP ✓

---

## 1.7 Fairness & Escalation (Judge)

### US-023 — Review the fairness / bias audit

**As a** Judge, **I want** to read the governance agent's fairness audit before recording any decision, **so that** I can see where the system may have over-reached.

- `FairnessCheck` row: `critical_issues_found`, `audit_passed`, `issues[]`, `recommendations[]`.
- UI presents each issue linked back to the specific reasoning step it flags.
- **Code:** `src/api/routes/judge.py::get_fairness_audit`
- **Status:** MVP ✓

### US-024 — Handle escalated cases (MVP partial)

**As a** Judge, **I want** escalated cases (complexity or fairness halt) to be clearly flagged with the reason and the partial analysis, **so that** I can decide how to proceed manually.

- `case.status = escalated` + `halt.reason` set by `complexity-routing` or `hearing-governance`.
- **Partial:** escalated cases appear in the list and carry the halt reason, but the manual-intervention workflow (re-assign, request additional docs, override escalation) is not yet wired.
- **Code:** `src/models/case.py::CaseStatus.escalated` · see `src/api/routes/cases.py::list_cases` filters
- **Status:** MVP (partial)

---

## 1.8 Decision & Hearing (Judge)

### US-025 — Record the judicial decision

**As a** Judge, **I want** to record my own decision at Gate 4 (verdict text, reasoning, notes), **so that** the case has an authoritative outcome attributed to me.

- `POST /cases/{id}/decision` sets `case.judicial_decision` and moves `status → closed`.
- Decision is append-only once committed; amendment is US-036 and is not-in-MVP.
- The system never generates a verdict; this endpoint is the one place a conclusion becomes binding.
- **Code:** `src/api/routes/cases.py::record_decision`
- **Status:** MVP ✓

### US-035 — Take in-hearing notes

**As a** Judge, **I want** to write private annotations during or after a hearing with section references, **so that** I have contemporaneous notes tied to the case.

- Free-form text with optional `section_reference` and `note_type`.
- Notes are lockable — once locked, they are append-only.
- Scoped to the authoring judge.
- **Code:** `src/api/routes/hearing_notes.py::create_hearing_note` · `src/models/case.py::HearingNote`
- **Status:** MVP ✓

### US-037 — Request case reopen

**As a** Judge, **I want** to submit a reopen request on a closed case with a stated reason, **so that** fresh evidence or procedural issues can be handled without side-door edits.

- `ReopenRequest` row captures requester, reason, status. Review workflow approves/rejects.
- **Code:** `src/api/routes/reopen_requests.py::create_reopen_request`
- **Status:** MVP ✓

### US-036 — Amend a recorded decision (Not in MVP)

Structured amendment with reason code and full audit delta. Parked.

---

## 1.9 Audit, Export, Navigation (Judge)

### US-026 — View the full audit trail

**As a** Judge, **I want** to inspect every agent invocation on a case — prompt, response, tool calls, model, token usage — **so that** I can defend the AI-assisted reasoning if challenged.

- `audit_logs` table populated from `CaseState.audit_log` at checkpoint time; also cross-referenced against MLflow runs.
- List + filter by agent name; each row expands into the full input/output payload.
- **Code:** `src/api/routes/audit.py::list_audit_logs`
- **Status:** MVP ✓

### US-027 — Export a case report / hearing pack

**As a** Judge, **I want** a PDF bundle with case summary, evidence, timeline, legal framework, arguments, hearing analysis, fairness audit, audit trail, **so that** I can bring it to the hearing and file it.

- WeasyPrint-based PDF export; optional "hearing pack" variant bundles the preparation-focused subset (absorbs US-020).
- **Code:** `src/api/routes/cases.py::export_case_report_pdf` · `src/api/routes/cases.py::export_hearing_pack`
- **Status:** MVP ✓

### US-028 — Search and filter my cases

**As a** Judge, **I want** to filter my case list by status, domain, complexity, date range, and free-text, **so that** I can find what I need.

- Full-text search on case description (FTS index from `0007_case_fts_index`).
- Faceted filters for status, domain, complexity tier.
- **Code:** `src/api/routes/cases.py::list_cases`
- **Status:** MVP ✓

### US-029 — See a dashboard overview

**As a** Judge, **I want** a dashboard with my active cases, cases awaiting my review, and recent escalations, **so that** I can triage my day.

- Aggregate counts by status + per-domain breakdown.
- "Awaiting your review" slice drives the judge's home screen.
- **Code:** `src/api/routes/dashboard.py::get_stats`
- **Status:** MVP ✓

---

## 1.10 Admin Console (Admin)

### US-031 — Manage the knowledge base and refresh vector stores

**As an** Admin, **I want** to upload, retire, and refresh documents in the domain knowledge base and trigger a vector-store refresh, **so that** the pipeline retrieves against current guidance.

- Domain-scoped upload with `llm-guard` classifier + regex sanitisation on every page at ingest time.
- "KB status" view (absorbs US-017) shows last-refreshed timestamps, document counts per domain, pending failures.
- Vector-store refresh triggers a re-sync of active domain documents against OpenAI vector stores.
- **Code:** `src/api/routes/admin.py::refresh_vector_store` · `src/api/routes/domains.py` · `src/api/routes/knowledge_base.py::get_knowledge_base_status`
- **Status:** MVP ✓

### US-032 — Monitor pipeline + integration health

**As an** Admin, **I want** a health dashboard showing PAIR circuit-breaker state, pipeline queue depth, and recent agent failures, **so that** I can spot problems early.

- PAIR circuit breaker probe + state endpoint.
- Queue depth, stuck-case counts, failure rates aggregated from `pipeline_jobs` and `audit_logs`.
- **Code:** `src/api/routes/health.py::pair_health` · `src/services/stuck_case_watchdog.py`
- **Status:** MVP ✓

### US-033 — Manage users and roles

**As an** Admin, **I want** to create and modify user accounts and assign roles, **so that** judges and admins have appropriate access.

- Create/disable users; assign one of `user`, `judge`, `senior_judge`, `admin`.
- Admin actions logged to `admin_events`.
- **Code:** `src/api/routes/admin.py::manage_user_action` · `src/models/admin_event.py`
- **Status:** MVP ✓

### US-034 — Configure cost + quota alerts

**As an** Admin, **I want** to set daily/monthly LLM cost ceilings and receive alerts before the pipeline would exceed them, **so that** one runaway case doesn't blow the budget.

- `system_config` row stores cost thresholds and enabled models.
- Breaching the ceiling short-circuits new pipeline runs with a visible admin-facing banner.
- **Code:** `src/api/routes/admin.py::set_cost_config` · `src/models/system_config.py`
- **Status:** MVP ✓

---

## 1.11 Out-of-MVP summary

Captured here only so reviewers can see what was consciously parked:

- **US-004** Dedicated rejection workflow with reason codes (jurisdiction failure is surfaced via US-003 today).
- **US-036** Structured decision amendment with audit delta (append-only decision today).
- **US-012** Anticipated-testimony edit flow (read-only in demo; Traffic-only data).

No ops-engineer stories are in scope for MVP. The `stuck_case_watchdog` CronJob and the MLflow tracking backend are operational support and are documented in [Part 6: CI/CD](06-cicd-pipeline.md) and [Part 11: AI Security Risk Register](11-ai-security-risk-register.md) rather than as user stories.

---
