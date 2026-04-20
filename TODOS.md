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

- **Status:** Complete in v0.1.0.0 (feat/group-b-judge-endpoints)

### Group C — Real-Time & Search

- **US-002: SSE/WebSocket Pipeline Status** — Real-time push updates for pipeline progress across 9 agents (currently no SSE)
- **US-028: Advanced Case Search & Filter** — Full-text search and advanced filtering on cases (domain, status, date range, description)

### Group D — Export & Reporting

- **US-003: Jurisdiction Validation Result Endpoint** — Dedicated endpoint to surface Agent 1 jurisdiction validation result
- **US-006: Evidence Analysis Dashboard Endpoint** — Aggregated endpoint for evidence strength, admissibility, and contradiction summaries
- **US-020: Hearing Pack Generation** — Compile and export a hearing preparation pack (case summary, evidence, arguments, verdict)
- **US-027: Case Report PDF Export** — Generate and download a PDF report of the full case analysis

## Technical Debt (from adversarial review, v0.1.0.0)

### Session Token Revocation
- **What:** `get_current_user` (deps.py) decodes the JWT but never validates against the `sessions` table. A revoked session remains valid until JWT expiry.
- **Why:** A fired/suspended judge retains API access for the JWT lifetime with no kill switch.
- **Priority:** P1 — security gap. Fix: check `Session.jwt_token_hash` and `expires_at` on every request.

### Verdict Ordering by UUID
- **What:** `get_fairness_audit` orders `Verdict` by `id.desc()` (UUID v4 — random, not temporal). The Verdict model has no `created_at` column.
- **Why:** On cases that re-run (via `return_to_pipeline`), the "most recent" verdict returned is random.
- **Priority:** P2 — add `created_at` to Verdict + migration, then change `order_by` to `created_at.desc()`.

### Redis Connection Leak in search_precedents.py
- **What:** `_get_redis_client()` creates a new `redis.Redis` object on every call, never closes them.
- **Why:** Under load this exhausts the Redis connection pool.
- **Priority:** P2 — convert to a module-level singleton with proper lifecycle management.
