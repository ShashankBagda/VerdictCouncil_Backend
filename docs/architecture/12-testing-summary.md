# Part 12: Testing Summary

Covers the test types, scope, tooling, and current results required by the grading rubric (§ 8 Group Report; § 8 Presentation). The authoritative test source is the `tests/` tree; the CI workflow at `.github/workflows/ci.yml` executes everything summarised here.

---

## 12.1 Test matrix

| Test Type | Scope | Tool / Method | Where | Pass Rate / Result |
|---|---|---|---|---|
| **Unit** | Pure-Python logic, agent nodes with faked LLM responses, single-function tools, middleware, schemas | `pytest` + `pytest-asyncio` + `pytest-mock` + `factory-boy` | `tests/unit/` (48 files) | Run in CI `unit-tests` job with `--cov-fail-under=65`; 100% of listed files execute clean on `main`. |
| **Integration — Pipeline graph** | End-to-end LangGraph run with real reducer + checkpointer, halt / fan-in conditions, SSE stream | `pytest` + live Postgres + in-process graph | `tests/pipeline/graph/` (`test_graph_builder.py`, `test_graph_sse.py`) | Runs locally; skipped in default CI path (no Postgres service yet) — target: dedicated `integration-tests` CI job. |
| **Integration — Infrastructure** | Halt conditions, pipeline-outbox claim semantics under concurrent load, stuck-case watchdog against a real DB, data-model migration 0019 | `pytest` + `testcontainers` (Postgres) | `tests/integration/test_halt_conditions.py`, `test_pipeline_jobs_outbox_pg.py`, `test_stuck_case_watchdog_pg.py`, `test_migration_0019.py` | Runs under `INTEGRATION_TESTS=1` locally. Target CI: `integration-tests` job with Postgres service. |
| **End-to-End (evaluation)** | Three curated test cases (two Traffic, one SCT) exercised through the full pipeline with expected per-agent outputs and what-if assertions | Bespoke runner: `tests/eval/eval_runner.py` driven by `fixtures.py` | `tests/eval/` | Run pre-demo and pre-release; results stored in MLflow experiment `verdictcouncil-pipeline`. See [Part 7 Appendix D](07-contestable-judgment-mode.md#appendix-d-evaluation-framework). |
| **Security — SAST** | Python source code for security antipatterns, OWASP Top-10 matches | `bandit -r src/`, `semgrep --config=p/security-audit --config=p/owasp-top-ten` | CI `sast` job; SARIF uploaded to GitHub Security tab | Advisory today (`continue-on-error: true`); target: block merges on medium+ findings. |
| **Security — SCA** | Dependency vulnerability scan + SBOM | `pip-audit --desc`, `safety check`, `cyclonedx-bom` | CI `sca` job | Advisory; SBOM published as build artefact. |
| **Security — DAST** | Live FastAPI instance behind a real Postgres; HTTP security-header check + API contract tests | CI job spins up `uvicorn` + Postgres service, runs `tests/integration/test_api_contract.py` | CI `dast` job | Advisory today. |
| **Security — Prompt-injection** | Document ingestion rejects known injection patterns; classifier catches novel ones | `tests/unit/test_sanitization.py` + `tests/unit/test_parse_document.py` | Runs in every CI unit-tests job | Hard-fail: regex layer is tested against a fixture list; classifier layer has a smoke test for load + inference. |
| **Performance / Load** | Per-agent latency and token usage budget | MLflow-tracked; dashboards in Grafana | Manual pre-release + MLflow queries | Targets: case-processing p95 ≤ 5s; evidence-analysis p95 ≤ 30s; argument-construction p95 ≤ 45s; full pipeline p95 ≤ 3min (excluding Gate pauses). Actuals tracked in MLflow, not yet fail-gated in CI. |

---

## 12.2 Test coverage by subsystem

| Subsystem | Unit | Integration | Notable gaps |
|---|---|---|---|
| Authentication + sessions | `test_auth.py`, `test_senior_judge_role.py` | — | CSRF double-submit middleware is not yet covered. |
| Case CRUD + serialization | `test_cases.py`, `test_case_serialization.py`, `test_case_state.py`, `test_validation.py` | `test_halt_conditions.py` | Edge cases around schema_version migration covered only indirectly. |
| Pipeline graph (agents + edges) | `test_graph_builder.py`, `test_graph_node_core.py`, `test_graph_state.py`, `test_graph_tools.py` | `tests/pipeline/graph/test_graph_builder.py` | Remote-dispatch mode (target architecture) has no dedicated test yet — blocked on agent Service skeleton landing. |
| Agent-specific | `test_judge_*`, `test_stability_score.py`, `test_what_if_controller.py`, `test_layer2_aggregator.py` (legacy; to be renamed for `gate2_join`) | — | `hearing-governance` strict-schema cases covered by `test_judge_fairness_audit.py`. |
| Tools | `test_confidence_calc.py`, `test_cross_reference.py`, `test_generate_questions.py`, `test_parse_document.py`, `test_search_domain_guidance.py`, `test_search_precedents.py`, `test_search_precedents_cache_key.py`, `test_timeline_construct.py`, `test_vector_store_fallback*.py` | — | PAIR live-search tests rely on the circuit breaker contract; no live-PAIR integration test (by design — avoid rate-limit contention). |
| External integrations | `test_pair_health.py`, `test_circuit_breaker.py` | `test_pipeline_jobs_outbox_pg.py` | MLflow instrumentation smoke-tested only. |
| Data layer | `test_persist_case_results.py`, `test_pipeline_state.py`, `test_pipeline_job_tasks.py` | `test_migration_0019.py`, `test_stuck_case_watchdog_pg.py` | Alembic up/down cycle not exhaustively covered. |
| Safety & security | `test_sanitization.py`, `test_rate_limit.py`, `test_exceptions.py`, `test_retry.py`, `test_domain_ingestion.py` | — | Adversarial prompt corpus needs expansion. |
| PDF / export | `test_pdf_export.py`, `test_hearing_pack.py` | — | — |
| Admin routes | `test_admin_routes.py`, `test_knowledge_base_*.py`, `test_domain_*.py`, `test_health_endpoint.py`, `test_precedent_search.py` | — | — |
| Frontend contract | `tests/integration/test_api_contract.py` | Runs in DAST CI job | Tests every endpoint the React client calls; detects breaking API changes before the frontend does. |

---

## 12.3 How to run tests locally

```bash
# Unit + fake-LLM agent tests (no infra needed)
make test

# With coverage report
make test-cov

# Integration tests (requires infra running)
make infra-up
INTEGRATION_TESTS=1 pytest tests/integration/ -v

# Evaluation fixtures (manual, uses OPENAI_API_KEY)
pytest tests/eval/ -v --tb=short

# Single module
pytest tests/unit/test_graph_builder.py -vv
```

CI reproduces `make test` plus the SAST/SCA/DAST matrix.

---

## 12.4 Key findings and limitations

### Findings

- **Structured outputs dramatically reduce hallucinations.** Switching `hearing-governance` to OpenAI strict-schema mode eliminated the "empty-issues-list-with-prose-apology" failure pattern observed in early fixtures. Covered by `tests/unit/test_judge_fairness_audit.py`.
- **Parallel Gate-2 safety depends on the reducer.** Early iterations of `_merge_case` lost data when two parallel agents touched overlapping fields. `tests/unit/test_graph_state.py` pins the expected merge semantics.
- **Outbox pattern prevents double-dispatch.** `test_pipeline_jobs_outbox_pg.py` proves `FOR UPDATE SKIP LOCKED` claim semantics under concurrent worker load; no job ran twice across 10 simulated workers.
- **Circuit breaker protects the pipeline from PAIR outages.** `test_circuit_breaker.py` + `test_vector_store_fallback.py` verify automatic fallback to the curated vector store within one failed request.

### Known limitations (tracked for follow-up)

- Integration tests are not run in CI; target is a dedicated `integration-tests` job with Postgres + Redis services.
- Coverage gate is 65%, target is 80%.
- SAST/SCA/DAST jobs run in advisory mode; target is hard failure on medium+ findings.
- Remote-dispatch mode (the canonical target architecture) has no dedicated tests yet — those depend on the per-agent Service skeleton landing in `src/agents/main.py`.
- No fuzz testing of the document ingestion pipeline against deliberately malformed inputs beyond the regex corpus.
- Load testing has not been exercised against the target per-agent topology; current benchmarks are against the local honcho setup.

---

## 12.5 Release gate

The release checklist (target; not enforced by CI yet) before a `release/*` → `main` merge:

- [ ] `make test-cov` passes locally with ≥ 80% coverage.
- [ ] `make test` passes in CI.
- [ ] SAST/SCA reports reviewed; any medium+ findings triaged or waived with justification.
- [ ] Evaluation suite (`tests/eval/`) run against staging; expected outputs match within tolerance for the three fixtures.
- [ ] Hallucination checklist ([Part 7 §D.7](07-contestable-judgment-mode.md#d7-hallucination-detection-checklist)) signed off per fixture.
- [ ] AI Security Risk Register reviewed for any new rows required by the release's feature set ([Part 11](11-ai-security-risk-register.md)).

---
