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

### Backend
- [ ] 1. `src/shared/case_state.py` — remove VerdictRecommendation, AlternativeOutcome, judge_decision; remove decided/rejected; rename deliberation→hearing_analysis
- [ ] 2. `src/models/case.py` — remove Verdict model, RecommendationType; remove decided/rejected from CaseStatus; rename Deliberation→HearingAnalysis (__tablename__="hearing_analyses")
- [ ] 3. `configs/agents/deliberation.yaml` → `hearing-analysis.yaml` — reframe step 7 from conclusion to hearing issues
- [ ] 4. `configs/agents/governance-verdict.yaml` → `hearing-governance.yaml` — remove Phase 2 verdict generation
- [ ] 5. `src/pipeline/runner.py` — update AGENT_ORDER, halt conditions, remove verdict refs
- [ ] 6. `src/db/persist_case_results.py` — remove _insert_verdict, remove Verdict from _clear_child_rows
- [ ] 7. Delete `src/api/routes/decisions.py`
- [ ] 8. `src/api/app.py` — remove decisions router, update description
- [ ] 9. `src/api/routes/senior_inbox.py` — remove amendment handling
- [ ] 10. `src/api/routes/cases.py` — remove review_rejected_case, verdict refs, update status groups
- [ ] 11. `src/api/routes/case_data.py` — remove get_case_verdict, rename deliberation→hearing_analysis
- [ ] 12. Delete `src/api/schemas/decisions.py`
- [ ] 13. `src/api/schemas/workflows.py` — remove amendment + rejection schemas
- [ ] 14. `src/api/schemas/cases.py` — remove VerdictResponse, verdict fields from responses
- [ ] 15. `alembic/versions/0016_remove_verdict_machinery.py` — DB migration
- [ ] 16. Delete `tests/unit/test_decisions.py` + update impacted tests
- [ ] 17. `TODOS.md` — remove verdict backlog items

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

### Task 11 — 72-hour staging shadow canary (operational)
- [ ] Deploy `settings.runner = "shadow"` to staging
- [ ] Run against production-shaped traffic for 72 h
- [ ] Review `runner_mode=shadow` MLflow experiment: confirm `match_ratio ≥ 0.95`, `diff_field_count` low
- [ ] Confirm diffs confined to known-variable prose fields (`hearing_analysis.pre_hearing_brief`, `arguments.*.summary`)
- [ ] No runner errors in staging logs

### Task 12 — `feat/switch-prod-to-graph`
- [ ] Flip `runner: Literal["mesh", "graph", "shadow"] = "mesh"` default to `"graph"` in `src/shared/config.py`
- [ ] Deploy and monitor one full review cycle in production
- [ ] Keep `"mesh"` callable via flag for rollback

### Task 13 — `feat/remove-sam` (execute after shadow parity passes)
Execute the deletion checklist in one PR on `feat/remove-sam` → `development`:
- [ ] Delete `configs/agents/*.yaml` (all 10), `configs/shared_config.yaml`, `configs/gateway/`, `configs/services/layer2-aggregator.yaml`, `configs/services/whatif-controller.yaml`
- [ ] Delete `src/pipeline/mesh_runner.py`, `mesh_runner_factory.py`, `_a2a_client.py`, `_solace_a2a_client.py`, `sam_status_translator.py`
- [ ] Delete `src/pipeline/hooks.py` (logic already ported to `graph/nodes/hooks.py`)
- [ ] Delete `src/pipeline/runner.py` (move `AGENT_ORDER`, `GATE_AGENTS` refs to `graph/prompts.py` — already done)
- [ ] Delete `src/tools/sam/` (whole dir); underlying domain tools in `src/tools/` are unchanged
- [ ] Delete `src/services/layer2_aggregator/`, `src/services/whatif_controller/` if SAM-bound
- [ ] Edit `docker-compose.infra.yml` — remove `solace` service block
- [ ] Edit `Makefile` — remove `solace-bootstrap` target; delete `scripts/solace-bootstrap.sh`
- [ ] Edit `Procfile.dev` — remove `.venv/bin/solace-agent-mesh run …` lines
- [ ] Edit `dev.sh` — remove Solace VPN boot lines (62-66)
- [ ] Edit `pyproject.toml` — drop `solace-agent-mesh>=0.5.0`, SAM mypy overrides
- [ ] Edit `src/shared/config.py` — remove `SOLACE_BROKER_*`, `WEB_GATEWAY_PORT`, `SESSION_SECRET_KEY`, `ADK_DATABASE_URL`; flip runner default to `"graph"`; drop `"mesh"`/`"shadow"` from Literal; drop `use_mesh_runner`
- [ ] Drop ADK Postgres DB: `DROP DATABASE verdictcouncil_adk;`
- [ ] Delete `VerdictCouncil_Backend/solace_ai_connector.log`
- [ ] Full suite green; dev env boots without broker; no `solace*` imports remain

### Task 14 — Root submodule bump (trunk commit on root repo)
After Task 13 merges to `main` in the backend:
- [ ] `cd /Users/douglasswm/Project/AAS/VER`
- [ ] `git add VerdictCouncil_Backend`
- [ ] `git commit -m "chore: bump backend to <sha> (remove SAM, migrate to LangGraph+MLflow)"`
- [ ] `git push origin main`
