# Part 2: System Architecture

---

## 2.1 Consolidation Rationale

The original design specified 18 specialized agents — one per logical task. While this maximized separation of concerns, it introduced unacceptable orchestration complexity: 17 inter-agent transitions, compounding latency, and token overhead from serializing/deserializing CaseState at every hop.

We consolidated to 9 agents using four guiding principles:

1. **Preserve the explainable decision pipeline.** The core reasoning chain — Evidence, Facts, Law, Arguments, Deliberation, Fairness, Verdict — must remain traceable. Each link in this chain stays as an independent graph node so the Judge can audit exactly where a conclusion originated.

2. **Bundle operational/administrative agents that perform logically sequential tasks.** Agents that always run in fixed order with no branching logic (e.g., intake then structuring then classification then jurisdiction) collapse into a single agent with sequential internal steps.

3. **Keep reasoning-heavy agents independent.** Agents that perform substantive legal reasoning (Evidence Analysis, Hearing Analysis) remain standalone. Their outputs are auditable decision points that the Judge reviews independently.

4. **Reduce orchestration complexity and token cost.** Fewer agents means fewer graph transitions, fewer state writes, and lower aggregate token consumption from repeated CaseState parsing.

### Consolidation Map

| # | Consolidated Agent | Original Agents Merged | Reduction |
|---|---|---|---|
| 1 | Case Processing | Case Intake + Case Structuring + Domain Classification + Jurisdiction Validation | 4 → 1 |
| 2 | Complexity & Routing | Complexity Assessment & Routing | 1 → 1 |
| 3 | Evidence Analysis | Evidence Analysis | 1 → 1 |
| 4 | Fact Reconstruction | Fact Extraction + Timeline Construction | 2 → 1 |
| 5 | Witness Analysis | Witness Identification + Testimony Anticipation + Credibility Assessment | 3 → 1 |
| 6 | Legal Knowledge | Legal Rule Retrieval + Precedent Retrieval | 2 → 1 |
| 7 | Argument Construction | Claim/Prosecution Advocate + Defense/Respondent Advocate + Balanced Assessment | 3 → 1 |
| 8 | Hearing Analysis | Hearing Analysis (renamed from Deliberation) | 1 → 1 |
| 9 | Hearing Governance | Fairness/Bias Audit + Gate 4 Review Preparation | 2 → 1 |
| | **Total** | | **18 → 9** |

**Net reduction:** 9 fewer transitions, approximately 50% reduction in orchestration overhead.

---

## 2.2 Orchestration Platform: LangGraph

VerdictCouncil runs on **[LangGraph](https://langchain-ai.github.io/langgraph/)**, a Python library for building stateful, graph-based agent workflows. The graph is constructed once in `src/pipeline/graph/builder.py` and executed in-process by either the FastAPI request handler (for synchronous previews) or the arq worker (for full pipeline runs).

LangGraph replaced the previous Solace Agent Mesh (SAM) + Google ADK stack in the responsible-AI refactor. The selection criteria were:

**Typed shared state.** A `GraphState` TypedDict (defined in `src/pipeline/graph/state.py`) flows through every node. Nodes read the fields they need and return partial updates; a custom reducer (`_merge_case`) safely merges the outputs of parallel branches back into the shared `CaseState` without losing data.

**In-process node dispatch.** Nodes are plain `async def` functions. There is no message broker, no serialization hop between nodes, and no cross-process IPC during a single pipeline run. Latency and token overhead are lower than the broker-based predecessor; the audit trail is preserved via the checkpointer + `audit_log` rather than broker message logs.

**Native conditional routing.** `add_conditional_edges(source, router_fn, {key: target})` declares branching at graph-build time. Routing functions are ordinary Python, so escalation and halt logic (see §2.6) lives next to the code it gates instead of being split across YAML topic subscriptions.

**Native parallel fan-out.** Declaring `add_edge(source, a); add_edge(source, b); add_edge(source, c)` fans out from `source` to all three targets, which execute concurrently. Fan-in is handled by a join node whose `_merge_case` reducer composes the parallel results (see §2.5.1).

**Postgres checkpointing.** `AsyncPostgresSaver` from `langgraph-checkpoint-postgres` persists the full `GraphState` to Postgres after every node. This is the substrate for crash recovery, audit replay, and what-if rewind — see `src/pipeline/graph/checkpointer.py`.

**OpenAI integration via `langchain-openai`.** Agent nodes use `ChatOpenAI` with tool binding and structured output (JSON mode for most agents, strict schema mode for `hearing-governance`). A thin wrapper in `src/pipeline/graph/nodes/common.py:_run_agent_node` standardises prompt assembly, tool dispatch, schema validation, and MLflow tracing.

**Runtime topology.** The FastAPI service (`src/api/app.py` on port 8001) accepts user actions and enqueues pipeline jobs; the arq worker (`src/workers/worker_settings.py`) claims jobs from the Postgres outbox and invokes the graph. Both processes share the same Docker image — the Procfile differs only in the entrypoint command.

---

## 2.3 Architecture Layers

The 9 reasoning agents are organized into 4 logical layers reflecting the judicial reasoning process. Three additional infrastructure nodes (`pre_run_guardrail`, `gate2_dispatch`, `gate2_join`, `terminal`) handle input sanitisation, fan-out dispatch, fan-in merge, and terminal SSE emission respectively.

```
┌─────────────────────────────────────────────────────────────────┐
│                    LAYER 1: CASE PREPARATION                    │
│  ┌─────────────────────┐    ┌──────────────────────────────┐   │
│  │  Case Processing     │───▶│  Complexity & Routing         │   │
│  │  (gpt-5.4-nano)      │    │  (gpt-5.4-nano)               │   │
│  └─────────────────────┘    └──────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────┤
│                 LAYER 2: EVIDENCE RECONSTRUCTION                │
│  ┌──────────────────┐ ┌──────────────────┐ ┌────────────────┐  │
│  │ Evidence Analysis │ │Fact Reconstruction│ │Witness Analysis│  │
│  │ (gpt-5)          │ │(gpt-5)           │ │(gpt-5-mini)    │  │
│  └──────────────────┘ └──────────────────┘ └────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                    LAYER 3: LEGAL REASONING                     │
│  ┌──────────────────────┐    ┌──────────────────────────────┐  │
│  │  Legal Knowledge      │───▶│  Argument Construction        │  │
│  │  (gpt-5)              │    │  (gpt-5.4)                    │  │
│  └──────────────────────┘    └──────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│               LAYER 4: HEARING PREPARATION & GOVERNANCE         │
│  ┌──────────────────────┐    ┌──────────────────────────────┐  │
│  │  Hearing Analysis     │───▶│  Hearing Governance           │  │
│  │  (gpt-5.4)            │    │  (gpt-5.4)                    │  │
│  └──────────────────────┘    └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

| Layer | Agents | Purpose | Model Tier |
|-------|--------|---------|------------|
| **Layer 1: Case Preparation** | case-processing, complexity-routing | Intake, structuring, jurisdiction validation, complexity assessment, routing | lightweight (gpt-5.4-nano) |
| **Layer 2: Evidence Reconstruction** | evidence-analysis, fact-reconstruction, witness-analysis | Analyse evidence, extract facts, build timeline, assess witnesses — runs in parallel | strong, strong, efficient |
| **Layer 3: Legal Reasoning** | legal-knowledge, argument-construction | Retrieve applicable law and precedents, construct both sides' arguments | strong, frontier |
| **Layer 4: Hearing Preparation & Governance** | hearing-analysis, hearing-governance | Produce hearing analysis with preliminary conclusion (Gate 3), audit for fairness before Gate 4 | frontier, frontier |

**Model assignment rationale:**
- **gpt-5.4-nano** for administrative tasks (parsing, structuring, complexity classification) — fast and cost-efficient.
- **gpt-5-mini** for witness analysis requiring efficient reasoning — good balance of reasoning capability and speed.
- **gpt-5** for evidence analysis, fact reconstruction, and legal retrieval — strong instruction-following for structured extraction with large context windows.
- **gpt-5.4** for deep reasoning tasks (argument construction, hearing analysis, hearing governance) — maximum reasoning capability for high-stakes judicial analysis.

Model assignments are resolved from `src/shared/config.py` (tier defaults, overridable via `OPENAI_MODEL_*` env vars) through the agent → tier map in `src/pipeline/graph/prompts.py`.

> For the complete technology matrix and model selection strategy, see [Part 4: Tech Stack](04-tech-stack.md).

---

## 2.4 CaseState as Shared Graph State

Every node reads and writes to a single shared `GraphState` object (defined in `src/pipeline/graph/state.py`). The field that carries domain data is `state["case"]`, a `CaseState` Pydantic model (`src/shared/case_state.py`) that grows monotonically as the pipeline progresses.

This pattern has three important properties:

1. **One in-process state, one reducer.** Nodes return partial updates; the custom `_merge_case` reducer combines them. Parallel-branch outputs merge without clobbering each other because each Gate-2 agent only touches its own fields; last-writer-wins applies only when two nodes intentionally update the same field.
2. **Checkpointer-backed durability.** After every node, `AsyncPostgresSaver` writes the full `GraphState` to Postgres keyed by `thread_id` (the pipeline `run_id`). A crash mid-run resumes from the last checkpoint with `is_resume=True`.
3. **Append-only audit.** `CaseState.audit_log` is a list; every agent appends an `AuditEntry` recording its inputs, outputs, system prompt, LLM response, tool calls, model, and token usage. The reducer dedupes entries (Pydantic `__eq__`) to survive retries without double-counting.

> **Dual-write pattern:** the `GraphState` checkpoint is the runtime source of truth. In parallel, each agent's output is projected into typed SQLAlchemy tables (`src/models/`) so the API can serve case CRUD, search/filter, and audit export without loading checkpoints. If these diverge, the checkpoint is authoritative and the tables can be reconciled from `CaseState.audit_log`.

### GraphState

```python
# src/pipeline/graph/state.py
class GraphState(TypedDict):
    case: Annotated[CaseState, _merge_case]  # custom reducer for parallel-safe merges
    run_id: str                              # pipeline-run UUID; doubles as thread_id
    extra_instructions: dict[str, str]       # per-agent retry hints (agent → text)
    retry_counts: dict[str, int]             # per-agent retry counter
    halt: dict[str, Any] | None              # escalation / halt flag, set by any node
    mlflow_run_ids: dict[str, tuple[str, str]]  # per-agent (run_id, experiment_id)
    is_resume: bool                          # True when resuming from a checkpoint
    start_agent: str | None                  # when set, skip to this node (gate rerun / what-if)
```

### CaseState Schema (summary)

The canonical schema lives in `src/shared/case_state.py`. Key fields, grouped by writer:

```python
class CaseState:
    schema_version: int = 2  # bumped when fields change incompatibly

    # --- Identity & Status (written by case-processing) ---
    case_id: str
    run_id: str
    parent_run_id: str | None                  # set for what-if runs, references the origin run
    domain: CaseDomainEnum | None              # small_claims | traffic_violation
    domain_vector_store_id: str | None         # domain KB vector store (optional)
    status: CaseStatusEnum                     # pending → processing → ... → ready_for_review / escalated / closed
    parties: list[dict]                        # [{name, role, contact, representation_status}]
    case_metadata: dict                        # filed_date, category, jurisdiction flags, ...

    # --- Documents (written by case-processing) ---
    raw_documents: list[dict]                  # [{doc_id, filename, file_id (OpenAI), type, submitted_by, description}]

    # --- Evidence (written by evidence-analysis) ---
    evidence_analysis: EvidenceAnalysis        # evidence_items, credibility_scores, ...

    # --- Facts (written by fact-reconstruction) ---
    extracted_facts: ExtractedFacts            # facts[], timeline[]

    # --- Witnesses (written by witness-analysis) ---
    witnesses: Witnesses                       # identified, testimony_anticipation, credibility

    # --- Law (written by legal-knowledge) ---
    legal_rules: list[dict]
    precedents: list[dict]

    # --- Arguments (written by argument-construction) ---
    arguments: dict

    # --- Hearing analysis + fairness (written by hearing-analysis / hearing-governance) ---
    hearing_analyses: list[HearingAnalysis]    # one entry per run; fairness_check nested inside

    # --- Judge decision (written after Gate 4 via API, not a graph node) ---
    judicial_decision: dict

    # --- Audit (appended by every node) ---
    audit_log: list[AuditEntry]
```

**Field ownership rules:**
- Each agent writes ONLY to its designated fields.
- Each agent MAY read any field written by a preceding agent.
- No agent may overwrite another agent's fields (the `_merge_case` reducer enforces this for parallel branches; sequential agents respect it by convention + schema validation).
- The `audit_log` is append-only; the reducer dedupes by entry equality.

---

## 2.5 Pipeline Flow

The full graph is built in `src/pipeline/graph/builder.py:build_graph()`. Every run enters through `pre_run_guardrail`, which checks submitted content for prompt injection and either halts or dispatches to `start_agent` (normally `case-processing` for new cases; `gate2_dispatch`, `argument-construction`, or similar for rerun / what-if modes).

```
                              START
                                │
                                ▼
                   ┌───────────────────────┐
                   │  pre_run_guardrail    │  injection scan → halt or route
                   └───────────────────────┘
                                │  (conditional: start_agent)
                                ▼
                   ┌───────────────────────┐
                   │   case-processing     │  intake + jurisdiction
                   └───────────────────────┘
                                │
                                ▼
                   ┌───────────────────────┐
                   │  complexity-routing   │  triage & route
                   └───────────────────────┘
                                │
                  ┌─────────────┼──────────────┐
           escalate_human   proceed        halt / pause
                  │             │              │
                  ▼             ▼              ▼
              terminal    gate2_dispatch     END
                              │
             ┌────────────────┼─────────────────┬────────────────┐
             ▼                ▼                 ▼                ▼
    ┌────────────────┐┌────────────────┐┌───────────────┐┌───────────────┐
    │evidence-analysis││fact-reconstruction││witness-analysis││legal-knowledge│
    └────────────────┘└────────────────┘└───────────────┘└───────────────┘
             │                │                 │                │
             └────────────────┴────────┬────────┴────────────────┘
                                       ▼
                            ┌────────────────────┐
                            │    gate2_join      │  fan-in barrier + retry router
                            └────────────────────┘
                                       │
                   ┌───────────────────┼──────────────────────┐
           retry Gate-2 agent   advance to L3             halt
                   │                   │                      │
                   ▼                   ▼                      ▼
               (agent re-run)   argument-construction     terminal
                                       │
                                       ▼
                               hearing-analysis  (self-loop on retry)
                                       │
                               ┌───────┴────────┐
                               ▼                ▼
                      hearing-governance     terminal
                               │
                      ┌────────┴────────┐
                      ▼                 ▼
                   terminal            END
```

**Key properties of the live graph (`src/pipeline/graph/builder.py`):**

| Property | Implementation |
|---|---|
| Entry | `pre_run_guardrail` (after `START`) |
| Parallel fan-out | `gate2_dispatch` → 4 edges → {evidence, fact, witness, legal-knowledge} |
| Parallel fan-in | all 4 Gate-2 agents → `gate2_join` (barrier reducer) |
| Retry | `gate2_join` can route back to any Gate-2 agent; `hearing-analysis` can loop to itself — both enforce a retry cap via `retry_counts[agent]` |
| Halt sink | Any node can set `state["halt"]`; conditional edges route to `terminal` (emits final SSE) → `END` |
| Rerun entry | `start_agent` in the initial `GraphState` bypasses earlier nodes — used by what-if scenarios and gate-by-gate rerun |

No agent ever invokes another agent directly — transitions are declarative edges in the graph. The topology is fixed at graph-build time; user-submitted content cannot alter it.

---

## 2.5.1 Gate-2 Fan-In Barrier (`gate2_join`)

LangGraph fans out from `gate2_dispatch` to four parallel nodes (evidence-analysis, fact-reconstruction, witness-analysis, legal-knowledge) via four outgoing edges. LangGraph handles the barrier automatically: `gate2_join` only fires after all four have returned, and the `_merge_case` reducer composes their partial `CaseState` updates into a single merged state.

### Responsibilities of `gate2_join`

Defined in `src/pipeline/graph/nodes/gate2_join.py`. Runs synchronously in-process after the parallel branches complete:

1. **Validate.** Inspect the merged `CaseState` to confirm each Gate-2 agent produced its expected fields (`evidence_analysis.evidence_items`, `extracted_facts.facts`, `witnesses.witnesses`, `legal_rules` + `precedents`).
2. **Decide retry vs advance.** If a field is empty or fails validation, route the conditional edge back to the owning agent with an `extra_instructions[agent]` entry explaining what went wrong. The retry is guarded by `retry_counts[agent]`; exceeding the cap halts the pipeline with an escalation reason.
3. **Advance.** When all four outputs validate, route to `argument-construction`.
4. **Halt.** Any unrecoverable condition (schema mismatch, repeated failure, downstream-blocking data quality issue) sets `state["halt"]` and routes to `terminal`.

### Why this replaces the prior Layer2Aggregator

The previous SAM-based design required a dedicated out-of-process **Layer2Aggregator** service to subscribe to three response topics and manually track per-case completion. With LangGraph the aggregator is a single node inside the graph: the barrier semantics come from the runtime, and the merge semantics come from the typed reducer. No separate pod, no Redis bookkeeping, no broker timeout — just a function.

---

## 2.6 Conditional Edges and Error Handling

### Halt Conditions

The pipeline has two explicit halt points where processing stops and the case is escalated to a human judicial officer:

**Halt Point 1 — Complexity & Routing.**

```
IF route == "escalate_human":
    state["halt"] = {"reason": "complexity_routing", ...}
    → terminal → END
```

Triggers: high complexity, potential precedent-setting impact, vulnerable parties without adequate safeguards, cross-jurisdictional issues requiring judicial discretion.

**Halt Point 2 — Hearing Governance (Fairness Audit).**

```
IF fairness_check.critical_issues_found:
    state["halt"] = {"reason": "fairness_audit", ...}
    → terminal → END
```

Triggers: systematic bias detected, reasoning relies on facts not in evidence, critical logical fallacies, demographic bias indicators, evidence from one party systematically overlooked.

The fairness audit is the final gate before the judge records a decision. If critical issues are found, the pipeline halts and the case is flagged. No AI verdict recommendation is ever produced — the judge decides.

### Retry Logic

`gate2_join` and `hearing-analysis` can loop back to re-run an agent with retry-specific instructions:

```
retry_counts[agent] += 1
if retry_counts[agent] > RETRY_CAP:
    state["halt"] = {"reason": f"{agent}_retry_exhausted", ...}
    → terminal
else:
    extra_instructions[agent] = "Missing credibility_scores; include one per witness."
    → agent  (re-runs with the extra instruction prepended to its user message)
```

### Error Handling

Failure inside a node propagates through LangGraph as an exception. The graph runner (`src/pipeline/graph/runner.py`) catches it, writes an error `AuditEntry`, sets `state["halt"]`, and routes the pipeline to `terminal`.

```
Failure scenarios:
├── LLM API timeout / 5xx      → retries inside _run_agent_node (exponential backoff), then HALT
├── Tool execution failure      → log tool error in audit_log, HALT (never skip a tool call)
├── JSON schema validation      → agent output rejected, HALT with the schema error attached
├── Payload size exceeded       → HALT, case flagged for manual processing
└── Worker crash                → checkpointer resumes from the last committed node when arq retries the job
```

The checkpointer guarantees at-least-once execution per node from the point of failure. Nodes are idempotent against the `audit_log` (dedup by entry equality) so a resumed run does not double-count.

---

## 2.7 Security and Prompt Injection Defenses

A judicial decision-support system processes adversarial input by definition — parties in a legal dispute have strong incentives to manipulate outcomes. The following defenses protect the pipeline from prompt injection and data manipulation attacks.

### Plan-Then-Execute Separation

The pipeline topology (which agents run, in what order) is fixed at deployment time in `src/pipeline/graph/builder.py`. No user-submitted content can alter the execution plan. Routing is determined by graph edges evaluated on typed state fields, not by LLM output.

### Privilege Separation

Agents that process untrusted content (case-processing, evidence-analysis) have no ability to modify the execution plan. They write only to their designated `CaseState` fields (enforced by the `_merge_case` reducer and per-agent schemas) and cannot invoke the next node directly — the graph runtime owns transitions. Even if an attacker successfully injects instructions into an evidence document, the compromised agent cannot skip the Governance audit or redirect the pipeline.

### Content Isolation

Raw documents are never placed in system prompts. The `parse_document` tool extracts structured data (text, tables, metadata) from uploaded files via the OpenAI Files API. The extracted content enters the LLM context as user-message content, not as system instructions, and is wrapped in `<user_document>...</user_document>` delimiters so the model distinguishes document content from instructions.

```
UNSAFE:  system_prompt = f"Analyze this document: {raw_document_content}"
SAFE:    system_prompt = "You are the Evidence Analysis Agent..."
         user_message  = "<user_document>...parsed_output...</user_document>"
```

### Input Sanitization Layer

Document-ingestion endpoints run a two-layer defence implemented in `src/shared/sanitization.py` and invoked from `src/pipeline/guardrails.py`:

1. **Regex fast-path.** Known injection patterns (`IGNORE PREVIOUS`, system prompt overrides, delimiter-escape attempts) are stripped or rejected.
2. **DeBERTa-v3 classifier** (`llm-guard`). Scores each page of domain uploads and judge-KB submissions; high-risk pages are rejected at ingest time.

The `pre_run_guardrail` node re-applies the same checks to case submissions at the start of every pipeline run, before any agent sees the content.

### Output Schema Validation

Every agent's output is validated against a Pydantic schema (`src/pipeline/agent_schemas.py`) before being written to `CaseState`. `hearing-governance` uses OpenAI strict-schema mode (no freeform drift); others use `json_object` mode + post-parse validation. Malformed output — including output that attempts to write to fields outside the agent's designated section — is rejected and the pipeline halts.

### Checkpointer + Audit Log as Immutable Audit Trail

Every node's inputs, outputs, and LLM calls are recorded as `AuditEntry` rows inside `CaseState.audit_log`. Immediately after each node, `AsyncPostgresSaver` writes the full `GraphState` — audit log included — to the `checkpoints` table. A tamper attempt on one record is detectable because the audit log, the per-agent typed projections in `src/models/`, and the MLflow per-agent run must all agree. The checkpoint row is the source of truth if they diverge.

### Hearing Governance Agent as Final Gate

The Hearing Governance Agent serves as the last line of defense. It audits the entire reasoning chain for logical consistency, unsupported claims, and bias. If the reasoning chain has been corrupted by injected content at any earlier stage, the Governance audit is designed to catch the resulting inconsistencies.

### Human-in-the-Loop (4-Gate HITL)

The pipeline pauses after each of four gates for judge review before proceeding. No AI verdict recommendation is ever generated — the judge reviews the governance summary at Gate 4 and records their own decision. The system is advisory only and cannot take autonomous action.

### Defense Summary

| Attack Vector | Defense | Layer |
|---------------|---------|-------|
| Prompt injection via documents | Content isolation + `parse_document` tool + `<user_document>` delimiters | Agent |
| Indirect injection at ingest | `llm-guard` regex + DeBERTa-v3 classifier (`src/shared/sanitization.py`) | Ingest |
| Per-run injection at dispatch | `pre_run_guardrail` graph node | Graph |
| Pipeline manipulation | Fixed topology in `builder.py`; graph edges evaluated on typed state | Platform |
| Output corruption | Pydantic schema validation + OpenAI strict-schema mode for governance | Pipeline |
| Bias injection | Governance fairness audit (`hearing-governance`) | Agent |
| Audit trail tampering | Checkpointer-backed `CaseState.audit_log` + MLflow cross-check | Platform |
| Unauthorized escalation bypass | Halt conditions enforced by conditional edges | Graph |
| Replay attacks | `run_id` + session-hash JWT + API rate limiting | Pipeline |

---
