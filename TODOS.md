# TODOS

## Pre-Production

### Solace HA/Clustering
- **What:** Configure Solace HA pair or migrate to Solace Cloud managed service
- **Why:** Single Solace broker is a single point of failure. Broker down = entire pipeline halts.
- **Context:** Architecture docs acknowledge this gap. Staging runs single broker. Production needs HA before go-live.
- **Depends on:** Phase 4 (K8s manifests) complete

### PAIR API Resilience
- **What:** Add health monitoring, distinguish 'no results' from 'API unreachable', implement fallback to curated vector store with UX indicator
- **Why:** PAIR Search API was discovered via network inspection (unofficial, reverse-engineered). Failures silently produce empty results, masquerading as "no precedents found."
- **Context:** PAIR covers higher courts only. SCT/traffic decisions already use vector store. Build resilience into search_precedents tool during Phase 4a.
- **Depends on:** Phase 4a (search_precedents tool)
- **Status:** Addressed in feat/pair-resilience — Redis-backed circuit breaker, OpenAI vector store fallback, health endpoint, dashboard integration

### Total Precedent Source Failure
- **What:** When both PAIR and vector store return empty results, the pipeline silently continues with zero precedents. Add a `precedent_unavailable` flag to CaseState to trigger a fairness audit warning.
- **Why:** Legal-knowledge agent currently treats zero results as "no relevant precedents" rather than "all sources failed." This distinction matters for governance and fairness checks.
- **Context:** Circuit breaker + vector store fallback reduce this risk but do not eliminate it. Both sources can legitimately return empty for novel fact patterns.
- **Depends on:** feat/pair-resilience complete
- **Status:** Addressed in feat/pre-production-fixes — `SearchResult` dataclass with metadata, `VectorStoreError` for failure distinction, `precedent_source_metadata` populated by runner, governance prompt updated. Note: only covers PipelineRunner path; SAM mesh path deferred.

### Cookie Secure Flag
- **What:** Make JWT cookie `secure` flag conditional on environment
- **Why:** `auth.py` sets `secure=True` unconditionally. Browsers won't send the cookie back over `http://localhost:8000`, breaking local dev auth.
- **Context:** Pre-existing bug discovered during OpenAPI spec review. Add a `settings.cookie_secure` flag that defaults to `True` in production and `False` in development.
- **Depends on:** Nothing
- **Status:** Addressed in feat/pre-production-fixes — `cookie_secure` setting with startup warning, centralized cookie kwargs

### Case Description Field
- **What:** Evaluate whether `Case` model needs a `description` column
- **Why:** `CaseCreateRequest` had a `description` field that was accepted but never stored. Removed during OpenAPI spec cleanup.
- **Context:** Field existed since initial case endpoint implementation. Either it was intended and forgotten, or it was dead code from the start. If a case description is useful, add a column + migration.
- **Depends on:** Nothing
- **Status:** Addressed in feat/pre-production-fixes — `description` column added with migration 0002, sanitized via `sanitize_user_input()` on create

## Future Scaling

### Redis Key Sharding for Layer2Aggregator
- **What:** Implement key sharding or migrate to Redis Cluster for the aggregator
- **Why:** Current design uses per-case Redis keys with Lua scripts. At 500+ concurrent cases, Redis single-thread serialization could bottleneck.
- **Context:** At projected 50-200 cases/month, this is a non-issue. Only matters at high concurrent volume.
- **Depends on:** Phase 3 (Layer2Aggregator) complete + production usage data

## Outstanding User Stories

### Group B — Judge-Facing Endpoints

- **US-009: Flag Disputed Facts** — Endpoint for judges to mark facts as disputed, updating fact status in the database
- **US-010: Evidence Gaps** — Endpoint surfacing what evidence is missing or insufficient for a case
- **US-016: Live Precedent Search (ad-hoc)** — User-facing endpoint to trigger ad-hoc PAIR API searches outside the pipeline
- **US-017: Knowledge Base Status** — Endpoint showing vector store health, coverage, and last-updated timestamps
- **US-023: Fairness & Bias Audit Display** — Dedicated endpoint to surface governance agent fairness check results
- **US-024: Escalated Cases Handling** — Escalation workflow with endpoint for judges to review and act on escalated cases

### Group C — Real-Time & Search

- **US-002: SSE/WebSocket Pipeline Status** — Real-time push updates for pipeline progress across 9 agents (currently no SSE)
- **US-028: Advanced Case Search & Filter** — Full-text search and advanced filtering on cases (domain, status, date range, description)

### Group D — Export & Reporting

- **US-003: Jurisdiction Validation Result Endpoint** — Dedicated endpoint to surface Agent 1 jurisdiction validation result
- **US-006: Evidence Analysis Dashboard Endpoint** — Aggregated endpoint for evidence strength, admissibility, and contradiction summaries
- **US-020: Hearing Pack Generation** — Compile and export a hearing preparation pack (case summary, evidence, arguments, verdict)
- **US-027: Case Report PDF Export** — Generate and download a PDF report of the full case analysis
