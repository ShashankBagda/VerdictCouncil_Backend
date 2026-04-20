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

### SAM mesh deploy is broken — every K8s pod would CrashLoopBackOff (URGENT)
- **What:** Multiple references in our SAM-mesh wiring point at Python modules that do **not exist** in `solace-agent-mesh==1.4.7`. As a result, **all 12 K8s deployments under `k8s/base/` would CrashLoopBackOff on first apply**, plus `make dev` (honcho) would crash on every SAM process at startup. Tracked end-to-end as ShashankBagda/VerdictCouncil_Backend#30.
- **Two distinct breakages:**
  1. **Wrong ENTRYPOINT.** `Dockerfile:30` runs `python -m solace_agent_mesh.main` and `Procfile.dev` does the same on every SAM line — but `solace_agent_mesh.main` does not exist in v1.4.7. Verified: `python -c "import solace_agent_mesh.main"` → `ModuleNotFoundError`. The actual SAM CLI entrypoint is `solace_agent_mesh.cli.main` (or the console scripts `sam` / `solace-agent-mesh`). This single mistake breaks every container the image powers, which is all 12 deployments.
  2. **Phantom aggregator app.** Even with the ENTRYPOINT fixed, `configs/services/layer2-aggregator.yaml:4` references `solace_agent_mesh.services.aggregator.app`, which also does not exist (`ModuleNotFoundError: No module named 'solace_agent_mesh.services'`). The architecture doc confirms SAM has no fan-in barrier (`docs/architecture/02-system-architecture.md:379`); the Layer2Aggregator was always meant to be a custom VerdictCouncil service we'd build (Epic 6 — `specs/cross-repo-gap-2026-04.md:428-473`, "still not started"). `src/services/layer2_aggregator/aggregator.py` is a spec/reference module — imported by nothing, doesn't satisfy the SAM app contract.
- **Why hidden today:** `staging-deploy.yml:30-42` only does `kubectl set image` (image-tag bumps on existing Deployments). No one has applied the `k8s/base/` manifests against a real cluster, so the bomb has not gone off. The actual production code path is `src/pipeline/runner.py` (single-process, "No Solace, no Redis" per its module docstring) — which uses zero SAM wiring. `grep "from solace_agent_mesh\|import solace_agent_mesh" src/ tests/` returns zero hits.
- **Why urgent now:** Section A of the Solace HA plan adds `kubectl apply -k k8s/overlays/staging` to CI. The moment that change ships, every SAM pod CrashLoopBackOff's on the next deploy.
- **Proposed fixes (smallest first), all detailed in #30:**
  1. One-line entrypoint fix in `Dockerfile` (×2 lines) and `Procfile.dev` (×12 lines): `solace_agent_mesh.main` → `solace_agent_mesh.cli.main`. Or switch to the `solace-agent-mesh` console script.
  2. Pick one for the dead aggregator config: (a) delete both files until Epic 6, (b) `replicas: 0` with `# EPIC-6-BLOCKED` comment, (c) move to `k8s/blocked/` outside the kustomize base.
  3. After 1+2, run `solace-agent-mesh --config configs/agents/case-processing.yaml` locally against `make infra-up` Solace and confirm at least one agent actually consumes a test message — there may be a third layer of "configured but never run" issues.
- **Context:** Found during eng-review of the Solace HA plan, 2026-04-17. Codex flagged the aggregator first; sub-agent investigation found the broader entrypoint bug; verified locally in the project venv. Initial scoping under-counted as 1 pod; actual scope is all 12.
- **Depends on:** Resolution before Section A of the Solace HA plan ships (Section A adds `kubectl apply -k` to staging-deploy.yml, which is what would trigger the crash-loop).

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
