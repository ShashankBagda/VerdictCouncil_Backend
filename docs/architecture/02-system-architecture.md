# Part 2: System Architecture

---

## 2.1 Consolidation Rationale

The original design specified 18 specialized agents — one per logical task. While this maximized separation of concerns, it introduced unacceptable orchestration complexity: 17 inter-agent transitions, compounding latency, and token overhead from serializing/deserializing CaseState at every hop.

We consolidated to 9 agents using four guiding principles:

1. **Preserve the explainable decision pipeline.** The core reasoning chain — Evidence, Facts, Law, Arguments, Deliberation, Fairness, Verdict — must remain traceable. Each link in this chain stays as an independent agent so the Judge can audit exactly where a conclusion originated.

2. **Bundle operational/administrative agents that perform logically sequential tasks.** Agents that always run in fixed order with no branching logic (e.g., intake then structuring then classification then jurisdiction) collapse into a single agent with sequential internal steps.

3. **Keep reasoning-heavy agents independent.** Agents that perform substantive legal reasoning (Evidence Analysis, Deliberation) remain standalone. Their outputs are auditable decision points that the Judge reviews independently.

4. **Reduce orchestration complexity and token cost.** Fewer agents means fewer Solace topic transitions, fewer payload serializations, and lower aggregate token consumption from repeated CaseState parsing.

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
| 8 | Deliberation | Deliberation Engine | 1 → 1 |
| 9 | Governance & Verdict | Fairness/Bias Audit + Verdict Recommendation | 2 → 1 |
| | **Total** | | **18 → 9** |

**Net reduction:** 9 fewer inter-agent transitions, approximately 50% reduction in orchestration overhead.

---

## 2.2 Orchestration Platform: Solace Agent Mesh

VerdictCouncil runs on **Solace Agent Mesh (SAM)**, an event-driven multi-agent framework built on the Solace PubSub+ Event Broker. SAM was selected over alternatives (LangGraph, CrewAI, AutoGen) for the following reasons:

**Event-Driven Async A2A Communication.** Agents communicate via the Solace Event Broker using a publish/subscribe model. Each agent subscribes to its input topic and publishes to the next agent's input topic. This decouples agents temporally — a slow Evidence Analysis does not block Case Processing from handling the next case.

**YAML-Driven Agent Configuration.** Each agent is defined in a standalone YAML file specifying its model, system prompt, tools, and broker connection. No Python orchestration code is needed for agent wiring — the topic structure IS the orchestration.

**SAM A2A Protocol.** Agents communicate using SAM's Agent-to-Agent protocol over Solace topics. The topic pattern is:

```
{namespace}/a2a/v1/agent/request/{target_agent_name}
```

For example, when Case Processing completes and needs to invoke Complexity & Routing:

```
verdictcouncil/a2a/v1/agent/request/complexity-routing
```

**No Built-in Shared State.** SAM does not provide a shared state store. This is a deliberate architectural constraint — it forces all inter-agent communication to flow through the event broker, creating a complete audit trail. The CaseState object is passed as the event payload through the pipeline (see Section 2.4).

**LiteLLM Wrapper for Model Abstraction.** SAM integrates with LLM providers through LiteLLM, allowing model specifications like `gpt-5.4`, `gpt-5`, `gpt-5-mini`, and `gpt-5.4-nano` to route to OpenAI's API without provider-specific code.

**Built-in Web Gateway.** SAM provides an HTTP/SSE gateway module that exposes the agent mesh to external clients. This serves as both the production API endpoint and a debugging/tracing interface during development.

**Enterprise-Grade Observability.** Every message that flows through the Solace Event Broker is auditable. Combined with SAM's built-in tracing, this provides a complete, immutable record of every agent invocation, every payload, and every response — critical for a judicial system.

**Fault-Tolerant Message Delivery.** The Solace Event Broker provides guaranteed message delivery with persistence. If an agent pod crashes mid-processing, the message remains in the broker queue and is redelivered when the agent recovers.

---

## 2.3 Architecture Layers

The 9 agents are organized into 4 logical layers reflecting the judicial reasoning process:

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
│                   LAYER 4: JUDICIAL DECISION                    │
│  ┌──────────────────────┐    ┌──────────────────────────────┐  │
│  │  Deliberation         │───▶│  Governance & Verdict         │  │
│  │  (gpt-5.4)            │    │  (gpt-5.4)                    │  │
│  └──────────────────────┘    └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

| Layer | Agents | Purpose | Model Tier |
|-------|--------|---------|------------|
| **Layer 1: Case Preparation** | Case Processing, Complexity & Routing | Intake, structuring, jurisdiction validation, complexity assessment, routing | gpt-5.4-nano, gpt-5.4-nano |
| **Layer 2: Evidence Reconstruction** | Evidence Analysis, Fact Reconstruction, Witness Analysis | Analyze evidence, extract facts, build timeline, assess witnesses | gpt-5, gpt-5, gpt-5-mini |
| **Layer 3: Legal Reasoning** | Legal Knowledge, Argument Construction | Retrieve applicable law and precedents, construct both sides' arguments | gpt-5, gpt-5.4 |
| **Layer 4: Judicial Decision** | Deliberation, Governance & Verdict | Reason from evidence to conclusion, audit for fairness, produce recommendation | gpt-5.4, gpt-5.4 |

**Model assignment rationale:**
- **gpt-5.4-nano** for administrative tasks (parsing, structuring, complexity classification) — fast and cost-efficient.
- **gpt-5-mini** for witness analysis requiring efficient reasoning — good balance of reasoning capability and speed.
- **gpt-5** for evidence analysis and legal retrieval — strong instruction-following for structured extraction with large context windows.
- **gpt-5.4** for deep reasoning tasks (argument construction, deliberation, governance) — maximum reasoning capability for high-stakes judicial analysis.

> For the complete technology matrix and model selection strategy, see [Part 4: Tech Stack](04-tech-stack.md).

---

## 2.4 CaseState as Event Payload

Since SAM has no built-in shared state mechanism, the entire case context travels as the event payload through the pipeline. Each agent receives the CaseState JSON, reads the fields relevant to its task, writes its output to its designated fields, and publishes the updated payload to the next agent's topic.

This pattern has three important properties:

1. **Self-contained messages.** Each event contains the complete case context. No database lookups are needed during agent processing ... the payload IS the runtime state.
2. **Immutable audit trail.** Each published event is a snapshot of the CaseState at that pipeline stage. The Solace broker retains these, creating a full history of how the case evolved.
3. **Stateless agents.** Agents hold no state between invocations. Any agent pod can process any case ... enabling horizontal scaling and fault tolerance.

> **Dual-write pattern:** The CaseState payload is the runtime source of truth during pipeline execution. However, each agent also persists its output to PostgreSQL for queryability (case CRUD, search/filter, audit export). The payload drives the pipeline; the database serves the API. If these diverge, the payload is authoritative and the database should be reconciled from the Solace message log.

### CaseState Schema

```python
class CaseState:
    """
    Central state object passed as JSON payload through the SAM pipeline.
    Each agent reads relevant fields and writes to its designated section.
    """

    # --- Identity & Status (written by Case Processing) ---
    case_id: str                    # UUID, assigned at intake
    run_id: str                     # UUID, generated per pipeline execution.
                                    # Uniquely identifies this run of the pipeline.
                                    # What-if scenario runs get a new run_id.
    parent_run_id: str | None       # UUID, nullable. References the run_id this
                                    # execution was derived from (for what-if
                                    # scenarios). None for initial pipeline runs.
                                    # Enables tracing the lineage of what-if analyses.
    domain: str                     # "small_claims" | "traffic_violation"
    status: str                     # "pending" | "processing" | "ready_for_review"
                                    # | "decided" | "rejected" | "escalated"
                                    # | "closed" | "failed"
    parties: list[dict]             # [{name, role, contact, representation_status}]
    case_metadata: dict             # {
                                    #   filed_date, category, subcategory,
                                    #   monetary_value (SCT), offence_code (traffic),
                                    #   jurisdiction_valid: bool,
                                    #   jurisdiction_issues: list[str]
                                    # }

    # --- Documents (written by Case Processing) ---
    raw_documents: list[dict]       # [{doc_id, filename, file_id (OpenAI), type,
                                    #   submitted_by, description}]

    # --- Evidence (written by Evidence Analysis) ---
    evidence_analysis: dict         # {
                                    #   items: [{doc_id, classification, strength,
                                    #            admissibility_risk, linked_claims,
                                    #            reasoning}],
                                    #   contradictions: [{doc_a, doc_b, description}],
                                    #   gaps: [str],
                                    #   corroborations: [{doc_ids, description}]
                                    # }

    # --- Facts (written by Fact Reconstruction) ---
    extracted_facts: dict           # {
                                    #   facts: [{fact_id, date, description, parties,
                                    #            location, source_refs, confidence,
                                    #            status: "agreed"|"disputed",
                                    #            conflicting_versions: [dict]}],
                                    #   timeline: [{timestamp, event, fact_id}]
                                    # }

    # --- Witnesses (written by Witness Analysis) ---
    witnesses: dict                 # {
                                    #   identified: [{name, role, relationship,
                                    #                 party_alignment, has_statement,
                                    #                 bias_indicators}],
                                    #   testimony_anticipation: [{witness_name,
                                    #                             summary, strong_points,
                                    #                             vulnerabilities,
                                    #                             conflicts_with_evidence}],
                                    #   credibility: [{witness_name, score: 0-100,
                                    #                  internal_consistency,
                                    #                  external_consistency,
                                    #                  bias_assessment,
                                    #                  specificity, corroboration}]
                                    # }

    # --- Law (written by Legal Knowledge) ---
    legal_rules: list[dict]         # [{statute, section, text, relevance_score,
                                    #   application_to_facts}]
    precedents: list[dict]          # [{citation, outcome, reasoning_summary,
                                    #   similarity_score, distinguishing_factors,
                                    #   source: "curated"|"live_search"}]

    # --- Arguments (written by Argument Construction) ---
    arguments: dict                 # Traffic: {prosecution, defense, contested_issues}
                                    # SCT: {claimant, respondent, agreed_facts,
                                    #        disputed_facts, evidence_gaps,
                                    #        strength_comparison}
                                    # Both include: {judicial_questions: [dict]}

    # --- Deliberation (written by Deliberation) ---
    deliberation: dict              # {
                                    #   established_facts, applicable_law,
                                    #   application: [{element, evidence, satisfied}],
                                    #   argument_evaluation,
                                    #   witness_impact, precedent_alignment,
                                    #   preliminary_conclusion,
                                    #   uncertainty_flags: [str]
                                    # }

    # --- Governance (written by Governance & Verdict) ---
    fairness_check: dict            # {
                                    #   balance_assessment, unsupported_claims,
                                    #   logical_fallacies, demographic_bias_check,
                                    #   evidence_completeness,
                                    #   precedent_cherry_picking,
                                    #   critical_issues_found: bool,
                                    #   audit_passed: bool
                                    # }
    verdict_recommendation: dict    # SCT: {recommended_order, amount, legal_basis,
                                    #        reasoning_summary}
                                    # Traffic: {recommended_verdict, sentence,
                                    #           sentencing_range}
                                    # Both: {confidence_score, uncertainty_factors,
                                    #         alternative_outcomes, fairness_report}

    # --- Judge (written externally after Judge review) ---
    judge_decision: dict            # {accepted, modified, rejected, notes, final_order}

    # --- Audit (appended by every agent) ---
    audit_log: list[dict]           # [{agent, timestamp, action,
                                    #   input_payload, output_payload,
                                    #   system_prompt, llm_response,
                                    #   tool_calls, model, token_usage,
                                    #   solace_message_id}]
```

**Field ownership rules:**
- Each agent writes ONLY to its designated fields.
- Each agent MAY read any field written by a preceding agent.
- No agent may overwrite another agent's fields.
- The `audit_log` is append-only — every agent adds its entry.

---

## 2.5 Pipeline Flow

The complete pipeline flow with SAM topic routing:

```
                         SAM Topic Routing
                         ─────────────────

    ┌──────────────┐  verdictcouncil/a2a/v1/agent/request/complexity-routing
    │    Case       │────────────────────────────────────────────────────────▶
    │  Processing   │
    └──────────────┘
                         ┌──────────────────┐
                         │  Complexity &     │
                    ◀────│  Routing          │
                    │    └──────────────────┘
                    │
              ┌─────┴─────┐
              │            │
         escalate     proceed
         _human       _automated / _with_review
              │            │
           [HALT]          │
                           │  .../request/evidence-analysis
                           │  .../request/fact-reconstruction
                           │  .../request/witness-analysis
                           ▼
                    ┌──────────────────────────────────────────┐
                    │     PARALLEL EXECUTION (fan-out)         │
                    │                                          │
                    │  ┌────────────┐ ┌────────────┐ ┌──────┐ │
                    │  │ Evidence   │ │   Fact     │ │Witnes│ │
                    │  │ Analysis   │ │Reconstrctn│ │Analys│ │
                    │  │ (Agent 3)  │ │ (Agent 4)  │ │(Ag 5)│ │
                    │  └────────────┘ └────────────┘ └──────┘ │
                    │                                          │
                    │  All three read same upstream data and   │
                    │  execute concurrently via topic fan-out. │
                    └──────────────────────────────────────────┘
                           │  .../response/evidence-analysis
                           │  .../response/fact-reconstruction
                           │  .../response/witness-analysis
                           ▼
                    ┌──────────────────────────────────────────┐
                    │      Layer2Aggregator (fan-in barrier)   │
                    │                                          │
                    │  Subscribes to all 3 agent output topics │
                    │  Tracks completion per case_id           │
                    │  Merges outputs into unified CaseState   │
                    │  Publishes to Agent 6 only when all 3    │
                    │  have completed for the same case_id     │
                    └──────────────────────────────────────────┘
                           │  .../request/legal-knowledge
                           ▼
                    ┌──────────────────┐
                    │ Legal Knowledge   │
                    └──────────────────┘
                           │  .../request/argument-construction
                           ▼
                    ┌──────────────────────┐
                    │ Argument Construction │
                    │                      │
                    │  ┌────────┐ ┌──────┐ │  (traffic cases: internal
                    │  │Prosecn.│ │Defense│ │   parallel analysis)
                    │  └────────┘ └──────┘ │
                    └──────────────────────┘
                           │  .../request/deliberation
                           ▼
                    ┌──────────────────┐
                    │   Deliberation    │
                    └──────────────────┘
                           │  .../request/governance-verdict
                           ▼
                    ┌──────────────────────┐
                    │ Governance & Verdict  │
                    │                      │
                    │  Phase 1: Audit      │
                    │     │                │
                    │  ┌──┴──┐             │
                    │  │     │             │
                    │ FAIL  PASS           │
                    │  │     │             │
                    │[HALT]  Phase 2:      │
                    │        Verdict       │
                    └──────────────────────┘
                           │  .../request/web-gateway
                           ▼
                    ┌──────────────────┐
                    │   Web Gateway     │
                    │   (HTTP/SSE)      │
                    └──────────────────┘
                           │
                           ▼
                      Judge's UI
```

**Full topic sequence:**

| Step | Source Agent | Target Topic | Target Agent |
|------|-------------|-------------|--------------|
| 1 | Web Gateway | `verdictcouncil/a2a/v1/agent/request/case-processing` | Case Processing |
| 2 | Case Processing | `verdictcouncil/a2a/v1/agent/request/complexity-routing` | Complexity & Routing |
| 3 | Complexity & Routing | `verdictcouncil/a2a/v1/agent/request/evidence-analysis` | Evidence Analysis (fan-out) |
| 3 | Complexity & Routing | `verdictcouncil/a2a/v1/agent/request/fact-reconstruction` | Fact Reconstruction (fan-out) |
| 3 | Complexity & Routing | `verdictcouncil/a2a/v1/agent/request/witness-analysis` | Witness Analysis (fan-out) |
| 4 | Evidence Analysis | `verdictcouncil/a2a/v1/agent/response/evidence-analysis` | Layer2Aggregator |
| 5 | Fact Reconstruction | `verdictcouncil/a2a/v1/agent/response/fact-reconstruction` | Layer2Aggregator |
| 6 | Witness Analysis | `verdictcouncil/a2a/v1/agent/response/witness-analysis` | Layer2Aggregator |
| 7 | Layer2Aggregator | `verdictcouncil/a2a/v1/agent/request/legal-knowledge` | Legal Knowledge |
| 7 | Legal Knowledge | `verdictcouncil/a2a/v1/agent/request/argument-construction` | Argument Construction |
| 8 | Argument Construction | `verdictcouncil/a2a/v1/agent/request/deliberation` | Deliberation |
| 9 | Deliberation | `verdictcouncil/a2a/v1/agent/request/governance-verdict` | Governance & Verdict |
| 10 | Governance & Verdict | `verdictcouncil/a2a/v1/agent/request/web-gateway` | Web Gateway |

**Parallel execution note:** For traffic cases, Argument Construction internally runs prosecution and defense analysis in parallel within the single agent invocation. This is handled at the prompt level (the model produces both analyses in one response) rather than at the orchestration level — no additional SAM topics are needed.

---

## 2.5.1 Layer 2 Aggregator — Fan-In Barrier Service

SAM's event broker provides native topic fan-out (one publisher, multiple subscribers), but does not provide a built-in fan-in barrier (wait for multiple publishers to complete before triggering a downstream consumer). The **Layer2Aggregator** is a lightweight stateful service that implements this barrier for Agents 3, 4, and 5.

### Purpose

After Complexity & Routing (Agent 2) publishes the CaseState, three agents execute concurrently:
- Agent 3 (Evidence Analysis) — writes `evidence_analysis`
- Agent 4 (Fact Reconstruction) — writes `extracted_facts`
- Agent 5 (Witness Analysis) — writes `witnesses`

Agent 6 (Legal Knowledge) requires outputs from all three before it can proceed. The Layer2Aggregator collects the three outputs, merges them into a single CaseState, and publishes to Agent 6's input topic only when all three have completed.

### Architecture

```
Agent 2 ─── fan-out ──┬── Agent 3 ──┐
                      ├── Agent 4 ──┤── Layer2Aggregator ── Agent 6
                      └── Agent 5 ──┘
```

### Behaviour

1. **Subscribe** to three response topics:
   - `verdictcouncil/a2a/v1/agent/response/evidence-analysis`
   - `verdictcouncil/a2a/v1/agent/response/fact-reconstruction`
   - `verdictcouncil/a2a/v1/agent/response/witness-analysis`

2. **Track completion** per `case_id:run_id` using an in-memory map (backed by Redis for crash recovery). The `run_id` (UUID) is generated per pipeline execution (or equals the `scenario_id` for what-if runs), ensuring concurrent executions for the same case are isolated:
   ```python
   pending = {
       f"{case_id}:{run_id}": {
           "evidence_analysis": None,   # or CaseState fragment
           "extracted_facts": None,
           "witnesses": None,
           "_original_case_state": None, # full CaseState from first receipt
       }
   }
   ```

3. **On each message received:**
   - Parse the `case_id`, `run_id`, and agent identifier from the topic/payload.
   - On first receipt for a given `case_id:run_id`, store the original full CaseState for later merge.
   - Store the agent's output in the corresponding slot.
   - Atomically check if all three slots are populated (via Redis Lua script to prevent duplicate publishes).
   - If yes: deep-copy the original CaseState, merge the three agent outputs into their designated fields (`evidence_analysis`, `extracted_facts`, `witnesses`), publish the full merged CaseState to `verdictcouncil/a2a/v1/agent/request/legal-knowledge`, and remove the `case_id:run_id` from the pending map.

4. **Timeout handling:** If fewer than 3 agents complete within 120 seconds, the aggregator halts the pipeline and sets the case status to `failed`. It logs which agents did not complete. Partial results are never forwarded to Agent 6 — incomplete analysis is worse than no analysis in a judicial context.

5. **Duplicate handling:** If the same agent publishes twice for the same `case_id` (e.g., due to broker retry), the aggregator overwrites the existing slot with the newer payload (idempotent).

> See [Part 3: Agent Configurations](03-agent-configurations.md#31-layer-2-aggregator) for the full YAML configuration.

> See [Part 3: Agent Configurations](03-agent-configurations.md#31-layer-2-aggregator) for the Python implementation.

> See [Part 5: Diagrams](05-diagrams.md#55-layer-2-aggregator-class-diagram) for the class diagram.

### Deployment

The Layer2Aggregator runs as an additional pod in the DOKS cluster (or container in docker-compose for local development). It is stateless except for the Redis-backed pending map, so it can be restarted without data loss.

| Component | Container Name | Port | Image |
|---|---|---|---|
| Layer2Aggregator | vc-layer2-aggregator | 8090 (health) | registry.digitalocean.com/verdictcouncil/layer2-aggregator |

This brings the total DOKS deployment to: **9 agent pods + 1 gateway + 1 What-If Controller + 1 Layer2Aggregator + 1 Solace broker = 13 pods**. PostgreSQL and Redis run as DigitalOcean Managed Services outside the cluster, accessed via private VPC networking.

---

## 2.6 Conditional Edges and Error Handling

### Halt Conditions

The pipeline has two explicit halt points where processing stops and the case is escalated to a human judicial officer:

**Halt Point 1: Complexity & Routing**

```
IF route == "escalate_human":
    status = "escalated"
    Pipeline HALTS
    Case queued for manual assignment
    Reason logged to audit_log
```

Triggers: high complexity, potential precedent-setting impact, vulnerable parties without adequate safeguards, cross-jurisdictional issues requiring judicial discretion.

**Halt Point 2: Governance & Verdict (Phase 1 Audit)**

```
IF critical_issues_found == true:
    status = "escalated"
    Pipeline HALTS BEFORE verdict generation
    Fairness audit report sent to Judge
    No verdict recommendation is produced
```

Triggers: systematic bias detected, reasoning relies on facts not in evidence, critical logical fallacies, demographic bias indicators, evidence from one party systematically overlooked.

The separation between audit and verdict within the Governance agent is critical: the fairness check MUST pass before any verdict recommendation is generated. A partial verdict (one that failed governance) must never reach the Judge.

### Error Handling

Any agent failure triggers the following sequence:

1. **Pipeline halts immediately.** No subsequent agents are invoked.
2. **Error is logged** to the `audit_log` with the failing agent name, error type, timestamp, and the CaseState snapshot at failure.
3. **Case status is set to `ERROR`** with a reference to the failing agent.
4. **Case is flagged for manual processing.** The Judge receives notification that automated analysis could not be completed, along with whatever partial analysis was produced by preceding agents.
5. **The Solace broker retains the failed message** for replay after the issue is resolved.

```
Agent failure scenarios:
├── LLM API timeout/error     → Retry 2x with exponential backoff, then HALT
├── Tool execution failure     → Log tool error, HALT (do not skip the tool)
├── JSON schema validation     → Agent output rejected, HALT
├── Payload size exceeded      → HALT, flag for manual processing
└── Broker delivery failure    → Solace handles retry with guaranteed delivery
```

---

## 2.7 Security and Prompt Injection Defenses

A judicial decision-support system processes adversarial input by definition — parties in a legal dispute have strong incentives to manipulate outcomes. The following defenses protect the pipeline from prompt injection and data manipulation attacks.

### Plan-Then-Execute Separation

The pipeline topology (which agents run, in what order) is fixed at deployment time in the SAM YAML configuration. No user-submitted content can alter the execution plan. The orchestration is determined by the Solace topic subscriptions, not by LLM output.

### Privilege Separation

Agents that process untrusted content (Case Processing, Evidence Analysis) have no ability to modify the execution plan. They can only write to their designated CaseState fields and publish to a single hardcoded next-agent topic. Even if an attacker successfully injects instructions into an evidence document, the compromised agent cannot skip the Governance audit or redirect the pipeline.

### Content Isolation

Raw documents are never placed in system prompts. The `parse_document` tool extracts structured data (text, tables, metadata) from uploaded files via the OpenAI Files API. The extracted content enters the LLM context as user-message content, not as system instructions. This prevents document content from being interpreted as system-level directives.

```
UNSAFE:  system_prompt = f"Analyze this document: {raw_document_content}"
SAFE:    system_prompt = "You are the Evidence Analysis Agent..."
         user_message  = f"Analyze the following extracted content: {parsed_output}"
```

### Input Sanitization Layer

All text extracted by `parse_document` is passed through an input sanitization layer before being included in downstream agent prompts. This layer: (a) strips known injection patterns (IGNORE PREVIOUS, system prompt overrides), (b) escapes special tokens, (c) wraps extracted text in XML delimiters (`<user_document>...</user_document>`) so the model can distinguish document content from instructions. This defense is applied at the tool output level, not the agent prompt level, ensuring all downstream consumers receive sanitized text.

### Output Schema Validation

Every agent's output is validated against a JSON schema before it is written to the CaseState payload and published to the next topic. Malformed output — including output that attempts to write to fields outside the agent's designated section — is rejected, and the pipeline halts.

### SAM Event Broker Audit Trail

Every message published to and consumed from the Solace Event Broker is logged with timestamp, source, destination, and payload hash. This creates a complete, immutable audit trail that cannot be modified by any agent. Any discrepancy between an agent's claimed output and the broker's recorded payload is detectable.

### Governance Agent as Final Gate

The Governance & Verdict Agent serves as the last line of defense. It audits the entire reasoning chain for logical consistency, unsupported claims, and bias before producing a verdict recommendation. If the reasoning chain has been corrupted by injected content at any earlier stage, the Governance audit is designed to catch the resulting inconsistencies.

### Human-in-the-Loop

No recommendation reaches the Judge without passing the Governance audit. The Judge retains full authority to accept, modify, or reject any recommendation. The system is advisory — it cannot take autonomous action.

### Defense Summary

| Attack Vector | Defense | Layer |
|---------------|---------|-------|
| Prompt injection via documents | Content isolation + parse_document tool | Agent |
| Pipeline manipulation | Fixed topology in YAML, topic-based routing | Platform |
| Output corruption | JSON schema validation | Pipeline |
| Bias injection | Governance audit (Phase 1) | Agent |
| Audit trail tampering | Solace broker immutable message log | Platform |
| Unauthorized escalation bypass | Halt conditions enforced at agent level | Agent |
| Replay attacks | Case ID + timestamp validation | Pipeline |

---
