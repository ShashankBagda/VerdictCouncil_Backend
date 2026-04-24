# Remove Verdict Machinery — Responsible AI Refactor

## Context
The system must not produce judicial decisions or verdict recommendations. It supports the judge
*for a hearing* only. AI verdict recommendations with accept/modify/reject UI induce automation
bias. This refactor scopes the system to hearing preparation only.

## Decisions
- **Statuses removed**: `decided`, `rejected` → existing rows mapped to `closed` via migration
- **Statuses kept**: `ready_for_review` (renamed meaning: "AI analysis complete, ready for hearing")
- **Rename**: `deliberation` → `hearing_analysis` everywhere (agent, CaseState field, DB table)
- **Rename**: `governance-verdict` → `hearing-governance` (fairness audit only, no verdict phase)
- **Migration**: verdicts table dropped; deliberations renamed to hearing_analyses; casestatus enum updated
- **Reopen**: kept, `appeal` reason removed (no verdict to appeal)

## Tasks

### Backend (all complete — see commit 8df5a64 + PR #73)
- [x] 1. `src/shared/case_state.py` — remove VerdictRecommendation, AlternativeOutcome, judge_decision; remove decided/rejected; rename deliberation→hearing_analysis
- [x] 2. `src/models/case.py` — remove Verdict model, RecommendationType; remove decided/rejected from CaseStatus; rename Deliberation→HearingAnalysis (__tablename__="hearing_analyses")
- [x] 3. `configs/agents/deliberation.yaml` → `hearing-analysis.yaml` — reframe step 7 from conclusion to hearing issues
- [x] 4. `configs/agents/governance-verdict.yaml` → `hearing-governance.yaml` — remove Phase 2 verdict generation
- [x] 5. `src/pipeline/runner.py` — deleted (SAM removed); LangGraph graph handles AGENT_ORDER
- [x] 6. `src/db/persist_case_results.py` — removed _insert_verdict, removed Verdict from _clear_child_rows
- [x] 7. Deleted `src/api/routes/decisions.py`
- [x] 8. `src/api/app.py` — removed decisions router, updated description
- [x] 9. `src/api/routes/senior_inbox.py` — removed amendment handling
- [x] 10. `src/api/routes/cases.py` — removed review_rejected_case, verdict refs, updated status groups
- [x] 11. `src/api/routes/case_data.py` — removed get_case_verdict, renamed deliberation→hearing_analysis
- [x] 12. Deleted `src/api/schemas/decisions.py`
- [x] 13. `src/api/schemas/workflows.py` — removed amendment + rejection schemas
- [x] 14. `src/api/schemas/cases.py` — removed VerdictResponse, verdict fields from responses
- [x] 15. `alembic/versions/0016_remove_verdict_machinery.py` — DB migration complete
- [x] 16. Deleted `tests/unit/test_decisions.py`; updated all impacted tests (run_from→run_what_if, clerk→judge role, single-judge ownership model)
- [x] 17. `TODOS.md` — verdict backlog items removed

## Done

---

# SAM → LangGraph + MLflow Migration

## Context
Migrate VerdictCouncil off Solace Agent Mesh to a LangGraph `StateGraph` over the existing `CaseState`. Cutover via parallel shadow-run; SAM deleted only after ≥95% field-match on gold-set corpus.

## Completed (Tasks 1–9 on `development`)
- [x] Task 1 — `feat/langgraph-scaffolding`: graph package skeleton, new deps, `settings.runner` Literal
- [x] Task 2 — `feat/langgraph-state-reducer`: `graph/state.py` (GraphState + `_merge_case` reducer), `graph/prompts.py` (all 9 agent prompts + constants)
- [x] Task 3 — `feat/langgraph-tools`: `graph/tools.py` `make_tools()` + `PrecedentMetaSideChannel`
- [x] Task 4 — `feat/langgraph-node-core`: `graph/nodes/common.py::_run_agent_node` (LLM+tool loop, SSE, MLflow, persist)
- [x] Task 5 — `feat/langgraph-agent-nodes`: 9 agent wrappers + `pre_run_guardrail`, `gate2_dispatch`, `gate2_join`, `terminal`
- [x] Task 6 — `feat/langgraph-builder`: `graph/builder.py` (compiled StateGraph, start_agent routing), `graph/checkpointer.py`
- [x] Task 7 — `feat/langgraph-sse-runner`: `graph/runner.py` (GraphPipelineRunner), `graph/sse.py`
- [x] Task 8 — `feat/runner-selector-wiring`: `settings.runner` wired into `cases.py` + `tasks.py`
- [x] Task 9 — `feat/shadow-runner`: `graph/shadow.py` (ShadowRunner + DeepDiff MLflow artifact)

## Remaining

### Task 10 — `feat/langgraph-parity-tests` ✅
Full test suite for the graph package.
- [x] `tests/pipeline/graph/test_graph_state.py` — reducer tests (in `tests/unit/test_graph_state.py`)
- [x] `tests/pipeline/graph/test_graph_tools.py` — tool subset + vector-store injection (in `tests/unit/test_graph_tools.py`)
- [x] `tests/pipeline/graph/test_graph_node_core.py` — node core unit tests (in `tests/unit/test_graph_node_core.py`)
- [x] `tests/pipeline/graph/test_graph_vs_mesh.py` — shadow-runner, _strip_volatile, _compute_match_ratio, ShadowRunner fallback; parity integration test is @skip(CI) — run manually in staging
- [x] `tests/pipeline/graph/test_graph_sse.py` — _is_terminal_event, subscribe() termination, astream_graph_events, node-core started/completed/agent_completed lifecycle
- [x] Acceptance: `uv run pytest tests/pipeline/graph/ -v` → 63 passed, 1 skipped

### Task 11 — 72-hour staging shadow canary (operational) — SKIPPED
Shadow runner removed along with SAM. LangGraph is now the only runner.

### Task 12 — `feat/switch-prod-to-graph` ✅
- [x] Flip `runner` default to `"graph"` in `src/shared/config.py`

### Task 13 — `feat/remove-sam` ✅
- [x] Deleted all 10 agent YAMLs, configs/gateway/, shared_config.yaml, services/layer2-aggregator.yaml
- [x] Deleted mesh_runner.py, mesh_runner_factory.py, _a2a_client.py, _solace_a2a_client.py, sam_status_translator.py, hooks.py, runner.py, shadow.py
- [x] Deleted src/tools/sam/, src/services/layer2_aggregator/
- [x] Removed solace service from docker-compose.infra.yml
- [x] Removed solace-bootstrap from Makefile; deleted scripts/solace-bootstrap.sh
- [x] Removed all SAM agent lines from Procfile.dev (api + arq-worker only)
- [x] Removed solace-agent-mesh from pyproject.toml; updated mypy overrides
- [x] Removed SOLACE_BROKER_* fields and runner Literal from config.py
- [x] Deleted all SAM k8s manifests; updated kustomization.yaml and ingress
- [x] Cleaned CI workflows (staging + production) of Solace secrets and SAM deployments
- [x] Tests: 102 graph-suite tests pass; no solace* imports remain in src/
- Note: solace_message_id is a nullable DB column left in place (no migration needed to remove it)

### Task 14 — Root submodule bump ✅
- [x] Bumped backend to 224a471 on root main; pushed to origin
