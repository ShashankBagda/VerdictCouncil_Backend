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
- **Status:** Addressed in feat/pre-production-fixes (PipelineRunner path) and feat/sam-precedent-metadata (SAM mesh path) — `SearchResult` dataclass with metadata, `VectorStoreError` for failure distinction, `precedent_source_metadata` populated by both runners, governance prompt updated. SAM wrapper writes metadata into `tool_context.state["precedent_source_metadata"]` (key exported as `PRECEDENT_META_STATE_KEY`); the SAM gateway must copy that value into the canonical `CaseState.precedent_source_metadata` before the next agent fires.

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
- **Status (2026-04-16):** Re-scoped after investigation. The deployed aggregator does NOT exist — `configs/services/layer2-aggregator.yaml:4` references `solace_agent_mesh.services.aggregator.app`, which is not a real module in `solace-agent-mesh==1.4.7` (verified via `python -c "import solace_agent_mesh.services.aggregator.app"` → `ModuleNotFoundError`). `src/services/layer2_aggregator/aggregator.py` is a spec/reference file, imported by nothing. There are no production Redis keys to shard. When the real local aggregator gets built (Epic 6 — SAM mesh activation), bake in Cluster-safe key shapes from day one (`vc:aggregator:{<case_id>}:<run_id>` with literal Redis hash-tag braces). See also the new "Dead Layer2Aggregator config" pre-production item below.

### Dead Layer2Aggregator config (URGENT)
- **What:** `configs/services/layer2-aggregator.yaml` and `k8s/base/deployment-layer2-aggregator.yaml` reference a SAM module (`solace_agent_mesh.services.aggregator.app`) that does not exist. If this Deployment is applied to a cluster, the pod will `CrashLoopBackOff` immediately with `ModuleNotFoundError: No module named 'solace_agent_mesh.services'`.
- **Why:** Today nothing applies this manifest (staging-deploy.yml only retags Deployments via `kubectl set image`, never `kubectl apply -k`), so the bomb hasn't gone off yet. But the moment anyone runs `kubectl apply -k k8s/overlays/staging` — which Section A of the Solace resilience plan adds to CI — the aggregator pod will start crash-looping. The architecture doc (`docs/architecture/02-system-architecture.md:379`) confirms SAM does not provide a fan-in barrier; the Layer2Aggregator was always meant to be a custom VerdictCouncil service that hasn't been built yet.
- **Options:** (a) delete both files until Epic 6 builds the real aggregator, (b) keep them with `replicas: 0` plus a clear `# EPIC-6-BLOCKED — do not enable` comment, (c) move the YAML out of `k8s/base/kustomization.yaml` into a separate `k8s/blocked/` directory so kustomize never picks it up.
- **Context:** Found during eng-review of the Solace HA plan, 2026-04-16. Codex flagged it; sub-agent investigation confirmed the SAM module is missing; verified locally in the project venv.
- **Depends on:** Decision before Section A of the Solace HA plan ships (Section A adds `kubectl apply -k` to staging-deploy.yml, which would trigger the crash-loop).

## Outstanding User Stories

### Group B — Judge-Facing Endpoints

- **Status:** Complete in v0.1.0.0 (feat/group-b-judge-endpoints)

### Group C — Real-Time & Search

- **US-002: SSE/WebSocket Pipeline Status** — Real-time push updates for pipeline progress across 9 agents (currently no SSE)
  - **Completed:** v0.3.0 (2026-04-16) — `GET /api/v1/cases/{id}/status/stream` (SSE) emits `PipelineProgressEvent` (`started`/`completed`/`failed`) per agent via Redis pub/sub. Pipeline runner instrumented in `src/pipeline/runner.py` (`_run_agent` wrapper + `_run_agent_inner`); helpers in `src/services/pipeline_events.py`; PR #24.
- **US-028: Advanced Case Search & Filter** — Full-text search and advanced filtering on cases (domain, status, date range, description)
  - **Completed:** v0.3.0 (2026-04-16) — `GET /api/v1/cases` extended with `q` (Postgres `to_tsvector @@ plainto_tsquery`, ILIKE fallback for short input), `date_from`, `date_to`. GIN index `ix_cases_description_fts` added in alembic 0004. PR #23.

### Group D — Export & Reporting

- **US-003: Jurisdiction Validation Result Endpoint** — Dedicated endpoint to surface Agent 1 jurisdiction validation result
- **US-006: Evidence Analysis Dashboard Endpoint** — Aggregated endpoint for evidence strength, admissibility, and contradiction summaries
- **US-020: Hearing Pack Generation** — Compile and export a hearing preparation pack (case summary, evidence, arguments, verdict)
  - **Completed:** v0.3.0 (2026-04-16) — `GET /api/v1/cases/{id}/hearing-pack` returns a zip via `src/services/hearing_pack.py`. Introduces shared `CaseReportData` projection in `src/services/case_report_data.py` (also used by US-027). PR #25.
- **US-027: Case Report PDF Export** — Generate and download a PDF report of the full case analysis
  - **Completed:** v0.3.0 (2026-04-16) — `GET /api/v1/cases/{id}/report.pdf` renders `src/templates/case_report.html` via WeasyPrint in `src/services/pdf_export.py`; Dockerfile updated with pango/cairo runtime deps. PR #26.

## Technical Debt (from adversarial review, v0.1.0.0)

### Session Token Revocation
- **What:** `get_current_user` (deps.py) decodes the JWT but never validates against the `sessions` table. A revoked session remains valid until JWT expiry.
- **Why:** A fired/suspended judge retains API access for the JWT lifetime with no kill switch.
- **Priority:** P1 — security gap. Fix: check `Session.jwt_token_hash` and `expires_at` on every request.
- **Completed:** 0172e3e (`fix(auth): validate JWT against sessions table on every request`) — `src/api/deps.py:68-86` joins `User`+`Session` and checks `jwt_token_hash` + `expires_at > now()` on every request.

### Verdict Ordering by UUID
- **What:** `get_fairness_audit` orders `Verdict` by `id.desc()` (UUID v4 — random, not temporal). The Verdict model has no `created_at` column.
- **Why:** On cases that re-run (via `return_to_pipeline`), the "most recent" verdict returned is random.
- **Priority:** P2 — add `created_at` to Verdict + migration, then change `order_by` to `created_at.desc()`.
- **Completed:** v0.2.0.0 (2026-04-15) — `created_at` added to Verdict model, ordering changed to `created_at DESC, id DESC`

### Redis Connection Leak in search_precedents.py
- **What:** `_get_redis_client()` creates a new `redis.Redis` object on every call, never closes them.
- **Why:** Under load this exhausts the Redis connection pool.
- **Priority:** P2 — convert to a module-level singleton with proper lifecycle management.
- **Completed:** feat/search-precedents-redis-singleton — module-level singleton in `src/tools/search_precedents.py`, `close_redis_client()` wired into FastAPI lifespan in `src/api/app.py`. Mirrors the pattern in `src/shared/circuit_breaker.py`.
