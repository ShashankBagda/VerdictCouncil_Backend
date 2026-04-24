# Part 3: Agent Configurations

This document describes the nine reasoning agents that run as LangGraph nodes in the VerdictCouncil pipeline. Each agent is an `async def` function in `src/pipeline/graph/nodes/` that reads `CaseState`, calls a `langchain-openai.ChatOpenAI` model with a system prompt and bound tools, validates the response against a Pydantic schema, and returns a partial `CaseState` update.

The pipeline topology (edges between agents, conditional routing, parallel fan-out) is defined declaratively in `src/pipeline/graph/builder.py`. See [Part 2 §2.5](02-system-architecture.md#25-pipeline-flow) for the flow diagram.

**Deployment shape.** Each agent is packaged as its own container image role and deployed as a distinct Kubernetes Deployment + Service in production. The Orchestrator invokes agents over HTTPS `POST /invoke` with an HMAC-signed `CaseState` payload; see [Part 2 §2.2](02-system-architecture.md#22-orchestration-platform) for the inter-service protocol and [Part 8](08-infrastructure-setup.md) for the K8s manifests. In local dev the Orchestrator calls the same handler function in-process to skip the HTTP hop (`DISPATCH_MODE=local`).

---

## 3.1 Canonical sources

| Concern | File |
|---|---|
| Graph construction (nodes + edges) | `src/pipeline/graph/builder.py` |
| Shared graph state (TypedDict + reducer) | `src/pipeline/graph/state.py` |
| `CaseState` Pydantic model | `src/shared/case_state.py` |
| Agent prompts, model tiers, tool bindings | `src/pipeline/graph/prompts.py` |
| Shared agent-run wrapper | `src/pipeline/graph/nodes/common.py::_run_agent_node` |
| Per-agent node modules | `src/pipeline/graph/nodes/<agent>.py` |
| Tool implementations | `src/tools/` + factory in `src/pipeline/graph/tools.py` |
| Output schemas (Pydantic) | `src/pipeline/agent_schemas.py` |
| Checkpointer | `src/pipeline/graph/checkpointer.py` |
| Runner | `src/pipeline/graph/runner.py` |

**Do not copy prompts from this file into code or test fixtures.** The verbatim prompt text lives in `AGENT_PROMPTS` in `src/pipeline/graph/prompts.py`; that dictionary is the single source of truth.

---

## 3.2 Agent catalog (at a glance)

| # | Agent | Model tier | Bound tools | Gate | Parallel? |
|---|---|---|---|---|---|
| 1 | `case-processing` | lightweight (gpt-5.4-nano) | `parse_document` | Gate 1 | — |
| 2 | `complexity-routing` | lightweight (gpt-5.4-nano) | — | Gate 1 | — |
| 3 | `evidence-analysis` | strong (gpt-5) | `parse_document`, `cross_reference` | Gate 2 | ✓ |
| 4 | `fact-reconstruction` | strong (gpt-5) | `timeline_construct` | Gate 2 | ✓ |
| 5 | `witness-analysis` | efficient (gpt-5-mini) | `generate_questions` | Gate 2 | ✓ |
| 6 | `legal-knowledge` | strong (gpt-5) | `search_precedents`, `search_domain_guidance` | Gate 2 | ✓ |
| 7 | `argument-construction` | frontier (gpt-5.4) | `confidence_calc` | Gate 3 | — |
| 8 | `hearing-analysis` | frontier (gpt-5.4) | — | Gate 3 | — |
| 9 | `hearing-governance` | frontier (gpt-5.4) | — | Gate 4 | — |

Gate mapping comes from `GATE_AGENTS` in `src/pipeline/graph/prompts.py`. Model-tier → environment-variable resolution lives in `MODEL_TIER_MAP` in the same file.

Three non-reasoning nodes also run in the graph:

| Node | Purpose |
|---|---|
| `pre_run_guardrail` | Prompt-injection scan on case submissions; conditional routing to `start_agent` |
| `gate2_dispatch` | Fan-out dispatch into the four parallel Gate-2 agents |
| `gate2_join` | Fan-in barrier + retry router for Gate 2 |
| `terminal` | Emits the final SSE event and sinks the run to `END` |

---

## 3.3 The shared agent wrapper (`_run_agent_node`)

Every reasoning agent delegates to `_run_agent_node()` in `src/pipeline/graph/nodes/common.py`. The wrapper standardises the lifecycle so node modules stay small (~30–80 lines each).

Responsibilities (in order):

1. **Enter MLflow run.** `pipeline.observability.agent_run(agent_name, run_id)` opens a nested MLflow run and yields `(mlflow_run_id, experiment_id)`.
2. **Resolve model.** Read `AGENT_MODEL_TIER[agent_name]`, map to a settings attribute via `MODEL_TIER_MAP`, read the model string off `Settings`.
3. **Assemble the `ChatOpenAI` client.** Bind the tools from `AGENT_TOOLS[agent_name]` (factory in `src/pipeline/graph/tools.py::make_tools(case_state)` — tools capture the active `case_id` / `domain_vector_store_id`).
4. **Build messages.**
   - System message: `AGENT_PROMPTS[agent_name]`, plus any `extra_instructions[agent_name]` appended (used on retry).
   - User message: agent-specific payload projected from the current `CaseState` (documents, prior analyses, open questions).
5. **Invoke the model.** Call `ChatOpenAI.ainvoke` (or `.astream` where streaming is wired). Tool calls are dispatched by the wrapper in a loop until the model returns content or the per-agent tool-call cap trips.
6. **Validate output.** Parse the response as JSON; validate against the agent's Pydantic schema in `src/pipeline/agent_schemas.py`. `hearing-governance` uses OpenAI strict-schema mode so validation is enforced at the SDK layer; others use `json_object` mode + post-parse validation.
7. **Write audit entry.** Append an `AuditEntry(agent, timestamp, action, input_payload, output_payload, system_prompt, llm_response, tool_calls, model, token_usage)` to `case.audit_log`.
8. **Return partial state.** Return only the fields the agent is allowed to write; the graph reducer merges them safely.

**Escape hatches:**

- **Retry request from downstream.** If `gate2_join` or `hearing-analysis` sends the graph back to an agent, `state["extra_instructions"][agent]` is set with a short human-readable hint. The wrapper appends it to the system prompt before re-invocation.
- **Halt.** If the wrapper raises, the runner catches, writes an error `AuditEntry`, sets `state["halt"]`, and routes to `terminal`.
- **Resume.** If `is_resume` is set and the agent already has a matching entry in `audit_log`, the wrapper skips the LLM call and returns the prior output — the checkpointer already has it.

---

## 3.4 Agent reference

Each section below documents the agent's contract, not its prompt text. The "Prompt summary" is a paraphrase for readers; the canonical wording lives in code.

### 3.4.1 `case-processing`

- **Module:** `src/pipeline/graph/nodes/case_processing.py`
- **Model tier:** `lightweight` (`gpt-5.4-nano`)
- **Tools:** `parse_document`
- **Reads:** `case_id`, `raw_documents`, `parties`, `domain`, `case_metadata`
- **Writes:** `case_metadata` (including `red_flags`, `jurisdiction_valid`, `jurisdiction_issues`), `parties`, enriched `raw_documents` entries (parsed type/kind), `status`
- **Prompt summary:** Run a rapid triage for red flags (fraud, cross-jurisdiction, protected persons, constitutional arguments, officials), parse every uploaded document via `parse_document`, trust the intake-form `Document.kind` tag before falling back to content-based inference, enrich `case_metadata`, and mark the case `processing`. Do not halt on red flags — record them and let `complexity-routing` decide.
- **Outgoing edge:** unconditional → `complexity-routing`
- **Halt conditions:** schema validation failure on `CaseState` projection; `parse_document` repeatedly fails on the same file; total ingest exceeds configured limits.

### 3.4.2 `complexity-routing`

- **Module:** `src/pipeline/graph/nodes/complexity_routing.py`
- **Model tier:** `lightweight` (`gpt-5.4-nano`)
- **Tools:** none
- **Reads:** `case_metadata` (incl. `red_flags`), `parties`, `domain`, `raw_documents[].doc_type`
- **Writes:** `case_metadata.complexity_tier`, `case_metadata.route_decision`, `status`
- **Prompt summary:** Classify the case into a complexity tier and emit one of three route decisions: `proceed_automated`, `proceed_with_review`, or `escalate_human`. Consider red flags from Step 1, jurisdiction issues, monetary value (SCT threshold), and cross-domain indicators.
- **Outgoing edges (conditional):**
  - `route_decision == "escalate_human"` → `terminal` (sets `halt.reason = "escalated_complexity"`)
  - `status` indicates pause / awaiting review → `END`
  - otherwise → `gate2_dispatch`

### 3.4.3 `gate2_dispatch`

- **Module:** `src/pipeline/graph/nodes/gate2_dispatch.py`
- **Model tier:** n/a (pure orchestration node)
- **Purpose:** Prepare `CaseState` for the Gate-2 fan-out and emit an SSE progress event. No LLM call.
- **Outgoing edges:** 4 parallel — `evidence-analysis`, `fact-reconstruction`, `witness-analysis`, `legal-knowledge`. LangGraph launches them concurrently.

### 3.4.4 `evidence-analysis`

- **Module:** `src/pipeline/graph/nodes/evidence_analysis.py`
- **Model tier:** `strong` (`gpt-5`)
- **Tools:** `parse_document`, `cross_reference`
- **Reads:** `raw_documents`, `parties`, `case_metadata`
- **Writes:** `evidence_analysis` (items, corroborations, contradictions, gaps, admissibility risk), `audit_log`
- **Prompt summary:** Classify every exhibit for admissibility and probative weight, use `cross_reference` to detect corroboration or contradiction between segments, and surface gaps that may prejudice later reasoning. Do not produce a verdict.
- **Concurrency:** Runs in parallel with `fact-reconstruction`, `witness-analysis`, `legal-knowledge`. The `_merge_case` reducer keeps only `evidence_analysis`; fields owned by peer agents are left untouched.
- **Retry:** `gate2_join` can route back with an `extra_instructions` hint if `evidence_items` is empty or critical admissibility flags are missing.

### 3.4.5 `fact-reconstruction`

- **Module:** `src/pipeline/graph/nodes/fact_reconstruction.py`
- **Model tier:** `strong` (`gpt-5`)
- **Tools:** `timeline_construct`
- **Reads:** `raw_documents`, `evidence_analysis` (if available via parallel pre-read; otherwise only `raw_documents`), `parties`
- **Writes:** `extracted_facts.facts[]` (each `{fact_id, date, description, parties, location, source_refs, confidence, status, conflicting_versions}`), `extracted_facts.timeline[]`
- **Prompt summary:** Extract atomic, source-anchored facts from primary documents; call `timeline_construct` to order them; mark each fact `agreed` or `disputed`; attach confidence in [0, 1] with explicit `source_refs`.
- **Retry:** back-routed by `gate2_join` if `facts[]` is empty, timeline is missing, or confidence values are out of range.

### 3.4.6 `witness-analysis`

- **Module:** `src/pipeline/graph/nodes/witness_analysis.py`
- **Model tier:** `efficient` (`gpt-5-mini`)
- **Tools:** `generate_questions`
- **Reads:** `raw_documents`, `parties`, `evidence_analysis` (if present)
- **Writes:** `witnesses.witnesses[]` (`name, role, relationship, party_alignment, has_statement, bias_indicators`), `witnesses.testimony_anticipation[]`, `witnesses.credibility` map (per-witness score 0–100 + dimensions)
- **Prompt summary:** Identify every witness, anticipate the evidentiary value of their testimony, and score credibility on internal consistency, external consistency, bias, specificity, and corroboration. Use `generate_questions` to surface probe questions the judge may want to ask.
- **Retry:** back-routed if `witnesses[]` is empty while documents reference witnesses, or if credibility scores omit required dimensions.

### 3.4.7 `legal-knowledge`

- **Module:** `src/pipeline/graph/nodes/legal_knowledge.py`
- **Model tier:** `strong` (`gpt-5`)
- **Tools:** `search_precedents`, `search_domain_guidance`
- **Reads:** `domain`, `domain_vector_store_id`, `case_metadata`, `extracted_facts` (if present from parallel reducer), `evidence_analysis` (if present)
- **Writes:** `legal_rules[]` (`{statute, section, text, relevance_score, application_to_facts}`), `precedents[]` (`{citation, outcome, reasoning_summary, similarity_score, distinguishing_factors, source}`)
- **Prompt summary:** Retrieve applicable statutes and precedents. Use `search_precedents` (PAIR Search API) for higher-court decisions; use `search_domain_guidance` for jurisdiction-specific guidance in the curated domain KB. Prefer precedent that is binding in the case's domain.
- **Resilience:** `search_precedents` is protected by the PAIR circuit breaker (`src/shared/circuit_breaker.py`). On open circuit, the tool falls back to a vector-store search against the curated precedent mirror (`src/tools/vector_store_fallback.py`).
- **Retry:** back-routed if neither precedents nor legal rules are produced for a case with non-trivial subject matter.

### 3.4.8 `gate2_join`

- **Module:** `src/pipeline/graph/nodes/gate2_join.py`
- **Model tier:** n/a (validator + router)
- **Purpose:** Barrier after the four parallel Gate-2 agents complete. Validates each agent's output, decides retry vs advance, and increments `retry_counts` where applicable.
- **Outgoing edges (conditional):** back to any of the four Gate-2 agents (on retry), forward to `argument-construction` (on success), or to `terminal` (on halt / retry-cap exhaustion).

### 3.4.9 `argument-construction`

- **Module:** `src/pipeline/graph/nodes/argument_construction.py`
- **Model tier:** `frontier` (`gpt-5.4`)
- **Tools:** `confidence_calc`
- **Reads:** `parties`, `evidence_analysis`, `extracted_facts`, `witnesses`, `legal_rules`, `precedents`
- **Writes:** `arguments` dict. Shape depends on `domain`:
  - **Traffic:** `{prosecution: {...}, defense: {...}, contested_issues: [...], judicial_questions: [...]}`
  - **SCT:** `{claimant: {...}, respondent: {...}, agreed_facts: [...], disputed_facts: [...], evidence_gaps: [...], strength_comparison: {...}, judicial_questions: [...]}`
- **Prompt summary:** Build both sides' strongest arguments and weigh them against the evidence and law gathered in Gate 2. Use `confidence_calc` to derive per-argument confidence from underlying evidence strength. Surface `judicial_questions` the hearing should address.

### 3.4.10 `hearing-analysis`

- **Module:** `src/pipeline/graph/nodes/hearing_analysis.py`
- **Model tier:** `frontier` (`gpt-5.4`)
- **Tools:** none
- **Reads:** `arguments`, `evidence_analysis`, `extracted_facts`, `witnesses`, `legal_rules`, `precedents`, `case_metadata`
- **Writes:** appends one entry to `hearing_analyses[]`. The Pydantic model declares four formal fields (`model_config = ConfigDict(extra="allow")`):
  ```
  HearingAnalysis(
      preliminary_conclusion: str | None,
      confidence_score: int | None,
      reasoning_chain: list[dict],
      uncertainty_flags: list[dict],
      # extra fields below are allowed-but-unstructured; the agent produces
      # them inside reasoning_chain or as top-level extras:
      #   established_facts, applicable_law, application, argument_evaluation,
      #   witness_impact, precedent_alignment
  )
  ```
  The corresponding SQLAlchemy table `hearing_analyses` has columns for the four formal fields only; the extras round-trip through `reasoning_chain` (JSONB). See `src/models/case.py::HearingAnalysis`.
- **Prompt summary:** Produce the judge-facing hearing analysis that walks through established facts → applicable law → application to facts → argument evaluation → preliminary conclusion, with explicit uncertainty flags. This is the Gate-3 artefact the judge reviews before the hearing.
- **Retry:** self-loop allowed via `gate2_join`-style router if the schema check fails (missing `preliminary_conclusion` or `application[]`). Retry cap enforced via `retry_counts["hearing-analysis"]`.

### 3.4.11 `hearing-governance`

- **Module:** `src/pipeline/graph/nodes/hearing_governance.py`
- **Model tier:** `frontier` (`gpt-5.4`)
- **Tools:** none
- **Reads:** the most recent `hearing_analyses[-1]`, `arguments`, `evidence_analysis`, `witnesses`, `legal_rules`, `precedents`, `case_metadata`, `parties`
- **Writes:** `CaseState.fairness_check` (top-level field, not nested inside `hearing_analyses`); updates `status` to `ready_for_review` or `escalated` depending on audit outcome
- **Output schema:** Strict — `FairnessCheck(critical_issues_found: bool, audit_passed: bool, issues: list[str], recommendations: list[str])` via OpenAI structured-output strict mode (`extra="forbid"` in `src/shared/case_state.py`).
- **Prompt summary:** Audit the hearing analysis for balance, unsupported claims, logical fallacies, demographic bias, evidence completeness, and precedent cherry-picking. Raise `critical_issues_found=true` if the reasoning cannot be trusted without judge remediation.
- **Outgoing edges (conditional):** `critical_issues_found` → `terminal` with `halt.reason = "fairness_audit"`; otherwise → `END`.
- **Design property:** This is the final graph-level gate. The judge records the actual decision through the API after Gate 4 review; no agent ever produces a verdict.

### 3.4.12 `terminal`

- **Module:** `src/pipeline/graph/nodes/terminal.py`
- **Purpose:** Emit the final SSE event with the halt reason (if any), update `status` accordingly, and sink to `END`. Runs on both success and halt paths.

---

## 3.5 Tool catalog

Tools are constructed per-run by `src/pipeline/graph/tools.py::make_tools(case_state)` so they can close over the active `case_id` and `domain_vector_store_id`. Each tool is a LangChain `@tool`-decorated callable.

| Tool | Source | Purpose |
|---|---|---|
| `parse_document` | `src/tools/parse_document.py` | Wraps the OpenAI Files API; extracts text, tables, and metadata. Runs `llm-guard` sanitisation on every page before returning. |
| `cross_reference` | `src/tools/cross_reference.py` | Compares two document segments for corroboration vs contradiction; returns a structured verdict with anchors. |
| `timeline_construct` | `src/tools/timeline_construct.py` | Orders a list of facts chronologically; resolves partial dates and conflicting versions deterministically. |
| `generate_questions` | `src/tools/generate_questions.py` | Produces probe questions for weak or vulnerable witness testimony. |
| `confidence_calc` | `src/tools/confidence_calc.py` | Aggregates evidence-strength, witness-credibility, and precedent-fit into a per-argument confidence score. |
| `search_precedents` | `src/tools/search_precedents.py` | Queries the PAIR Search API. Guarded by a Redis-token-bucket rate limiter and a circuit breaker; falls back to `vector_store_fallback.py` on open circuit. Results are Redis-cached with `precedent_cache_ttl_seconds` TTL. |
| `search_domain_guidance` | `src/tools/search_domain_guidance.py` | Vector-store search over the curated domain KB (`settings.openai_vector_store_id` or the per-domain override in `domain_vector_store_id`). |

---

## 3.6 Output schemas

All agent output schemas live in `src/pipeline/agent_schemas.py`. They are Pydantic models with `extra="allow"` except for `FairnessCheck` (`hearing-governance`), which uses `extra="forbid"` to hold the governance output to OpenAI's strict structured-output mode.

The wrapper in `_run_agent_node` parses the LLM response with the appropriate schema before writing to `CaseState`. Validation failure raises; the runner catches, records the failure in `audit_log`, and routes to `terminal`.

---

## 3.7 Retry, halt, and resume semantics

- **`retry_counts[agent]`** — incremented by the router node (`gate2_join`, or the self-router on `hearing-analysis`) every time the graph loops back. Exceeding the per-agent cap forces a halt.
- **`extra_instructions[agent]`** — set by the router with a short hint describing what failed (e.g., `"credibility_scores missing 'internal_consistency' for W-002"`). Appended to the system prompt on the retry invocation.
- **`state["halt"]`** — any node may set this dict (`{reason, details}`). Subsequent conditional edges route to `terminal`, which emits the final SSE and ends the run.
- **`is_resume`** — the arq worker sets this when retrying a job after a crash. The wrapper checks `audit_log` for a matching entry before calling the model; if found, the prior output is reused and the node skips re-running the LLM (checkpointer consistency guarantee).

---

## 3.8 Observability per agent

Every agent emits:

1. **MLflow run** — nested under the pipeline run; logs model, prompt, response, tool calls, and token usage via `openai.autolog()`. Experiment: `settings.mlflow_experiment` (default `verdictcouncil-pipeline`).
2. **`AuditEntry`** — identical payload written to `CaseState.audit_log` and persisted with the next checkpoint.
3. **SSE progress event** — emitted from graph nodes via the pipeline's streaming channel for the frontend `CaseWorkbench`.
4. **Structured stdout line** — JSON log including `agent`, `case_id`, `run_id`, `duration_ms`, `tokens_in`, `tokens_out`, `tool_calls`, and outcome.

The three channels let operators reconcile any suspected data drift: checkpoint, MLflow run, and stdout log must agree; if they do not, the checkpoint is authoritative (§2.7).

---

## 3.9 Extending the agent set

To add a new agent:

1. Add its name to `AGENT_ORDER`, `AGENT_MODEL_TIER`, and `AGENT_TOOLS` in `src/pipeline/graph/prompts.py`.
2. Add its prompt to `AGENT_PROMPTS`.
3. Write a node module under `src/pipeline/graph/nodes/<name>.py`; the body usually delegates to `_run_agent_node(agent_name="...", state=state)` and then enriches/returns a partial `CaseState`.
4. Add a Pydantic output schema to `src/pipeline/agent_schemas.py`.
5. Wire the node into `src/pipeline/graph/builder.py`: add the node and any edges (direct or conditional).
6. Extend `CaseState` in `src/shared/case_state.py` if the agent owns new fields — bump `schema_version` and write a migration in `alembic/versions/` for any projected columns.
7. Add tests: a fake-LLM unit test hitting the node directly, and an integration test covering the new edge cases.

Do not add an agent "around" the graph (e.g., as a second FastAPI service). The invariants described in §2.7 rely on the whole pipeline being a single graph; a shadow agent breaks the audit guarantees.

---
