# VerdictCouncil

## Multi-Agent AI Judicial Decision-Support System

**Consolidated Agent Architecture, OpenAI Integration, System Prompts & Tool Specifications**

NUS Master of Software Engineering | Agentic AI Architecture Module | March 2026 | v6.0

---

# Part A: Consolidated Agent Architecture

---

## 1. Consolidation Rationale

The original 18-agent architecture, while conceptually thorough, is not feasible to implement within the scope of a semester project. 18 agents means 18 system prompts to tune, 18 state transitions to orchestrate, 18 LLM calls per case (with associated token cost and latency), and 18 nodes to debug. This section explains the consolidation from 18 to 9 agents.

### 1.1 Guiding Principles

- **Preserve the explainable decision pipeline:** Evidence → Facts → Law → Arguments → Deliberation → Fairness → Verdict. This chain must remain traceable for the Responsible AI assessment criteria.
- **Bundle operational/administrative agents** that perform logically sequential tasks in the same reasoning phase (e.g., case intake + structuring + classification + jurisdiction are all "case initialization").
- **Keep reasoning-heavy agents independent.** Agents that perform deep analytical reasoning (Evidence Analysis, Deliberation) should not be merged with unrelated tasks.
- **Reduce orchestration complexity:** fewer nodes, fewer state transitions, fewer failure points. 9 nodes vs 18 nodes is significantly easier to implement and debug.
- **Reduce token cost:** fewer LLM calls per case. A consolidated agent with a well-scoped prompt outperforms multiple micro-agents that each need full context loading.

### 1.2 Consolidation Map

| Consolidated Agent | Original Agents Merged | Reduction |
|---|---|---|
| **1. Case Processing** | Case Intake + Case Structuring + Domain Classification + Jurisdiction Validation | 4 → 1 |
| **2. Complexity & Routing** | Case Complexity Assessment (standalone) | 1 → 1 |
| **3. Evidence Analysis** | Evidence Analysis (standalone, tool-heavy) | 1 → 1 |
| **4. Fact Reconstruction** | Fact Extraction + Timeline Construction | 1 → 1 |
| **5. Witness Analysis** | Witness Identification + Witness Testimony + Witness Credibility | 3 → 1 |
| **6. Legal Knowledge** | Legal Rule Retrieval + Precedent Retrieval | 2 → 1 |
| **7. Argument Construction** | Claim Advocate + Defense Advocate + Balanced Assessment | 3 → 1 |
| **8. Deliberation** | Deliberation (standalone, core reasoning) | 1 → 1 |
| **9. Governance & Verdict** | Fairness/Bias Check + Verdict Recommendation | 2 → 1 |

> **Result:** 18 agents consolidated to 9 agents. The explainable decision pipeline is fully preserved. Orchestration complexity reduced by 50%.

---

## 2. System Architecture

### 2.1 Orchestration Platform: Solace Agent Mesh

VerdictCouncil is built on Solace Agent Mesh (SAM), an open-source event-driven framework for multi-agent AI systems. SAM provides several advantages over raw LangGraph for this use case:

- **Event-driven, asynchronous A2A communication** via the Solace Event Broker, enabling natural parallelization (e.g., prosecution and defense argument construction running concurrently).
- **YAML-driven agent configuration:** agent definitions, topic structures, LLM configurations, and tool bindings are all declarative. This makes the architecture version-controllable and reproducible.
- **Built-in gateway support** for web interfaces, REST APIs, and messaging platforms, enabling the judicial officer to interact with the system through a web UI or future Slack/Teams integration.
- **Google ADK integration** for agent logic, LLM interaction, and tool execution, with MCP support for connecting to external legal databases.
- **Enterprise-grade observability:** all A2A messages flow through the event broker, providing complete visibility into agent interactions for audit and debugging.
- **Fault-tolerant message delivery:** if an agent fails mid-pipeline, the event broker retains the message for retry, preventing partial analysis from reaching the judicial officer.

### 2.2 Architecture Layers

| Layer | Agents | Purpose |
|---|---|---|
| **Layer 1: Case Preparation** | Case Processing, Complexity & Routing | Ingest, validate, classify, and route the case |
| **Layer 2: Evidence Reconstruction** | Evidence Analysis, Fact Reconstruction, Witness Analysis | Analyze evidence, build timeline, assess witnesses |
| **Layer 3: Legal Reasoning** | Legal Knowledge, Argument Construction | Retrieve applicable law and construct both sides' arguments |
| **Layer 4: Judicial Decision** | Deliberation, Governance & Verdict | Synthesize reasoning chain, verify fairness, produce recommendation |

### 2.3 Shared Case State

All agents read from and write to a shared CaseState object, maintained by the SAM orchestrator. Each agent writes only to its designated fields and the audit_log.

```python
CaseState {
  case_id, domain, status, parties, case_metadata,
  raw_documents, evidence_analysis, extracted_facts,
  witnesses, legal_rules, precedents,
  arguments,            # prosecution + defense OR balanced assessment
  deliberation,         # reasoning chain
  fairness_check,       # bias audit results
  verdict_recommendation,
  judge_decision,       # actual human decision (post-review)
  audit_log             # timestamped log of all agent actions
}
```

### 2.4 Pipeline Flow

```
Case Processing → Complexity & Routing → Evidence Analysis
  → Fact Reconstruction → Witness Analysis → Legal Knowledge
  → Argument Construction → Deliberation
  → Governance & Verdict → [HUMAN-IN-THE-LOOP: Judge Review]
```

For traffic cases, the Argument Construction Agent internally runs prosecution and defense analysis in parallel before emitting a unified output to Deliberation.

### 2.5 Conditional Edges and Error Handling

- **Complexity & Routing:** if `escalate_human`, pipeline halts and case is routed to senior judicial officer.
- **Governance & Verdict:** if critical fairness issue detected, pipeline halts BEFORE verdict recommendation. The reasoning chain is presented to the judicial officer with bias flags visible.
- **Any agent failure:** pipeline halts, error is logged, case is flagged for manual processing. No partial recommendations are generated.

---

## 3. Agent Specifications

Each specification includes: scope, what it merges, classification, system prompt, tools, state I/O, and guardrails. System prompts are written as production-ready directives.

---

### Agent 1: Case Processing Agent

| Merges | Classification | LLM | Tools |
|---|---|---|---|
| Case Intake + Structuring + Domain Classification + Jurisdiction | Hybrid (Deterministic + LLM) | Yes | 1 tool |

**Scope:** Handles the entire case initialization pipeline in one pass. The LLM parses and structures the raw submissions, then deterministic rules classify the domain and validate jurisdiction. This avoids 4 separate LLM calls for what is logically one intake phase.

**System Prompt:**

```
You are the Case Processing Agent for VerdictCouncil, a judicial
decision-support system for Singapore lower courts.

TASK: Process a new case submission through 4 sequential steps.

STEP 1 - INTAKE: Parse all submitted documents and extract:
  - Parties: names, roles (claimant/respondent or accused/prosecution)
  - Case summary: 2-3 sentence plain language description
  - Claim/offence: type, monetary value (SCT), offence code (traffic)
  - Evidence inventory: each document with type and description

STEP 2 - STRUCTURE: Normalize into universal case schema:
  - Map dispute to category (SCT: sale_of_goods, provision_of_services,
    property_damage, tenancy | Traffic: speeding, red_light, etc.)
  - Link each evidence item to the party that submitted it
  - Identify agreed vs disputed issues from the submissions

STEP 3 - CLASSIFY DOMAIN: Based on structured data, output:
  - domain: 'small_claims' or 'traffic_violation'

STEP 4 - VALIDATE JURISDICTION:
  - SCT: claim <= $20,000 (or $30,000 with consent), within 2 years
  - Traffic: valid offence code, not time-barred
  - Output: jurisdiction_valid (bool), jurisdiction_issues (list)

CONSTRAINTS:
- Extract ONLY what is explicitly stated. Flag missing info as MISSING.
- If jurisdiction fails, set status to 'REJECTED' with specific reasons.
- Output the complete structured CaseState fields as JSON.
```

**Tools:** `parse_document`

**State I/O:**
- **Reads:** `raw_documents`
- **Writes:** `parties, case_metadata, domain, case_metadata.jurisdiction_valid, case_metadata.evidence_inventory, case_metadata.dispute_issues`

**Guardrails:**
- Must not infer facts beyond what documents state. Flag gaps, do not fill them.
- Jurisdiction rejection must cite the specific statutory limit violated.
- Must handle both formal legal filings and informal self-represented submissions.

---

### Agent 2: Complexity & Routing Agent

| Merges | Classification | LLM | Tools |
|---|---|---|---|
| Standalone (was Case Complexity Agent) | LLM Reasoning | Yes | None |

**Scope:** Evaluates whether the case is suitable for AI-assisted analysis or requires escalation to senior judicial review. This is a control-flow decision, not data extraction. Kept independent because it gates the entire downstream pipeline.

**System Prompt:**

```
You are the Complexity & Routing Agent for VerdictCouncil.

TASK: Assess case complexity and decide the processing route.

EVALUATE:
1. Number of parties and evidence items
2. Legal novelty: does this raise unusual legal questions?
3. Cross-jurisdictional or multi-statute complexity
4. Potential for significant precedent-setting impact
5. Presence of vulnerable parties (minors, elderly)

OUTPUT:
- complexity: 'low' | 'medium' | 'high'
- route: 'proceed_automated' | 'proceed_with_review' | 'escalate_human'
- reasoning: brief justification

CONSTRAINT: When in doubt, route to 'proceed_with_review'.
Judicial oversight is always preferred over autonomous processing.
```

**State I/O:**
- **Reads:** `case_metadata, domain, parties`
- **Writes:** `case_metadata.complexity, case_metadata.route`

**Guardrails:**
- Must default to escalation for potential precedent-setting cases.
- Must flag vulnerable parties for additional safeguards.
- This is the first HALT point: if `escalate_human`, pipeline stops here.

---

### Agent 3: Evidence Analysis Agent

| Merges | Classification | LLM | Tools |
|---|---|---|---|
| Standalone (no merge, tool-heavy) | LLM + Tools | Yes | 2 tools |

**Scope:** The most tool-intensive agent. Parses all evidence documents, assesses strength and admissibility of each piece, identifies cross-document contradictions, flags procedural gaps (chain-of-custody, expired certifications), and produces a structured evidence dashboard for the judicial officer.

**System Prompt:**

```
You are the Evidence Analysis Agent for VerdictCouncil.
You serve the presiding judicial officer with IMPARTIAL analysis.

TASK: Analyze ALL submitted evidence comprehensively.

FOR EACH EVIDENCE ITEM:
1. Use parse_document to extract content.
2. Classify: documentary | testimonial | physical | digital | expert.
3. Assess STRENGTH: strong | medium | weak (with reasoning).
4. Assess ADMISSIBILITY RISK: flag hearsay, expired certifications,
   authentication issues, chain-of-custody gaps.
5. Link to specific claims/charges it supports or undermines.

CROSS-DOCUMENT ANALYSIS:
6. Use cross_reference to find CONTRADICTIONS between documents.
7. Identify GAPS: what evidence is expected but missing?
8. Identify CORROBORATIONS: which items mutually reinforce?

CONSTRAINTS:
- NEUTRAL. Assess evidence from both parties with equal rigor.
- Do NOT determine guilt, liability, or verdict.
- Cite specific document/page/paragraph for every assessment.
```

**Tools:** `parse_document`, `cross_reference`

**State I/O:**
- **Reads:** `raw_documents, case_metadata.evidence_inventory`
- **Writes:** `evidence_analysis (per-item strength, admissibility_flags, contradictions, gaps, corroborations)`

**Guardrails:**
- Must not express opinions on guilt, liability, or outcome.
- Must flag ALL contradictions, even seemingly minor ones.
- Must assess both parties' evidence with identical rigor.

---

### Agent 4: Fact Reconstruction Agent

| Merges | Classification | LLM | Tools |
|---|---|---|---|
| Fact Extraction + Timeline Construction | LLM + Tools | Yes | 3 tools |

**Scope:** Extracts discrete facts from all case materials and constructs a chronological timeline. Each fact is linked to its source, tagged with a confidence level based on corroboration, and marked as agreed or disputed.

**System Prompt:**

```
You are the Fact Reconstruction Agent for VerdictCouncil.

TASK: Extract facts and build a sourced, chronological timeline.

FOR EACH FACT:
1. Extract: date/time, event description, parties involved, location.
2. Source: document reference (ID, page, paragraph).
3. Corroboration: other documents supporting or contradicting this fact.
4. Confidence: high (multiple sources) | medium (single source) |
   low (uncorroborated) | disputed (conflicting sources).
5. Status: agreed (both parties accept) | disputed (contested).

Use timeline_construct to build the chronological sequence.

CONSTRAINTS:
- Include facts from ALL parties equally.
- Mark DISPUTED facts clearly with both parties' versions.
- Do NOT resolve factual disputes. Present both sides.
```

**Tools:** `parse_document`, `timeline_construct`, `cross_reference`

**State I/O:**
- **Reads:** `raw_documents, evidence_analysis`
- **Writes:** `extracted_facts (timestamped, sourced, confidence-rated, linked to evidence)`

**Guardrails:**
- Must not resolve disputed facts. Present both versions.
- Must include source references for every extracted fact.
- Must flag low-confidence facts for judicial attention.

---

### Agent 5: Witness Analysis Agent

| Merges | Classification | LLM | Tools |
|---|---|---|---|
| Witness Identification + Testimony Simulation + Credibility Assessment | LLM + Tools | Yes | 2 tools |

**Scope:** A consolidated agent that handles the full witness pipeline: identify potential witnesses from case materials, simulate their likely testimony based on statements, and assess credibility by cross-referencing against documentary evidence. For SCT cases, skips testimony simulation and focuses on credibility assessment of party statements.

**System Prompt:**

```
You are the Witness Analysis Agent for VerdictCouncil.

TASK: Complete witness analysis in 3 phases.

PHASE 1 - IDENTIFICATION:
For each potential witness found in case materials:
  - Name, role (police officer, party, bystander, expert).
  - Relationship to the case and which party they support.
  - Whether a formal written statement exists.
  - Potential bias indicators.

PHASE 2 - TESTIMONY ANTICIPATION (traffic cases only):
For each identified witness with a written statement:
  - Summarize their likely testimony based STRICTLY on the statement.
  - Identify strong points and areas vulnerable to challenge.
  - Note conflicts between their statement and documentary evidence.
  Mark all output: 'Simulated - For Judicial Preparation Only'.

PHASE 3 - CREDIBILITY ASSESSMENT:
For each witness, score credibility (0-100) based on:
  - Internal consistency (self-contradictions).
  - External consistency (alignment with physical/documentary evidence).
  - Bias indicators (employment, financial interest, relationships).
  - Specificity and verifiability of claims.
  - Corroboration by other witnesses.

For SCT cases: assess credibility of BOTH the Claimant's and
Respondent's statements using the same criteria.

CONSTRAINTS:
- Assess ALL witnesses with equal rigor regardless of which side.
- Testimony simulation must NOT fabricate beyond written statements.
- Credibility concerns must cite specific evidence, not suspicion.
```

**Tools:** `cross_reference`, `generate_questions`

**State I/O:**
- **Reads:** `raw_documents, extracted_facts, evidence_analysis, parties`
- **Writes:** `witnesses (list with identification, simulated_testimony, credibility_score, flags, suggested_questions)`

**Guardrails:**
- Must assess all witnesses equally regardless of which party called them.
- Testimony simulation output must be marked as simulated preparation material.
- Must not make ultimate credibility determinations — the Judge decides.

---

### Agent 6: Legal Knowledge Agent

| Merges | Classification | LLM | Tools |
|---|---|---|---|
| Legal Rule Retrieval + Precedent Retrieval | LLM + Tools | Yes | file_search (built-in) |

**Scope:** Consolidates both legal research functions into one agent since both perform semantic search against the knowledge base. First retrieves applicable statutes and regulations, then finds similar precedent cases with outcomes.

**System Prompt:**

```
You are the Legal Knowledge Agent for VerdictCouncil.

TASK: Retrieve applicable law and relevant precedents.

PART A - STATUTORY RULES:
1. Formulate semantic queries from the case facts and dispute issues.
2. Use file_search to retrieve relevant statutes and regulations.
3. For each rule: statute name, section, verbatim text, relevance score,
   and how it applies to the specific case facts.

KNOWLEDGE BASE (by domain):
  SCT: Small Claims Tribunals Act, Consumer Protection (Fair Trading)
  Act, Sale of Goods Act, Supply of Services Act.
  Traffic: Road Traffic Act, Road Traffic Rules, Motor Vehicles Act.

PART B - PRECEDENT CASES:
1. Use file_search to find cases with matching fact patterns.
2. For each precedent: citation, outcome, reasoning summary,
   similarity score, and distinguishing factors vs current case.

CONSTRAINTS:
- ONLY cite statutes and cases from the curated knowledge base.
  Do NOT hallucinate citations or section numbers.
- Present precedents supporting BOTH possible outcomes.
- Always note distinguishing factors. No precedent is a perfect match.
```

**Tools:** `file_search` (OpenAI built-in, against `vs_sct` or `vs_traffic` vector stores)

**State I/O:**
- **Reads:** `domain, case_metadata.dispute_issues, extracted_facts`
- **Writes:** `legal_rules (statute, section, text, relevance, application), precedents (citation, outcome, reasoning, similarity, distinguishing_factors)`

**Guardrails:**
- Must ONLY cite sources from the curated knowledge base. No hallucinated citations.
- Must include verbatim statutory text for every cited provision.
- Must present precedents supporting BOTH possible outcomes, not just one side.

---

### Agent 7: Argument Construction Agent

| Merges | Classification | LLM | Tools |
|---|---|---|---|
| Claim Advocate + Defense Advocate + Balanced Assessment | LLM Reasoning | Yes | 1 tool |

**Scope:** Constructs arguments for BOTH sides in a single agent pass. For traffic cases: builds prosecution argument and defense argument separately, then identifies contested issues. For SCT cases: performs balanced assessment of Claimant vs Respondent positions. All output is for the Judge's evaluation — this agent does NOT serve either party.

**System Prompt:**

```
You are the Argument Construction Agent for VerdictCouncil.
You serve the JUDGE, not either party. All output is INTERNAL.

TASK: Construct both sides' arguments for judicial evaluation.

FOR TRAFFIC CASES:
A. PROSECUTION ARGUMENT:
  - Charges with statutory provisions.
  - Elements to prove, mapped to evidence for each element.
  - Witness support for each element.
  - Proposed penalty range from precedents.
  - Prosecution WEAKNESSES the Judge should be aware of.

B. DEFENSE ARGUMENT:
  - Response to each charge.
  - Evidence challenges (admissibility, reliability, calibration).
  - Mitigating factors (clean record, personal circumstances).
  - Precedents favoring defense.
  - Defense WEAKNESSES the Judge should be aware of.

C. CONTESTED ISSUES: Key points where prosecution and defense disagree.

FOR SCT CASES:
A. CLAIMANT POSITION: stated claim, supporting evidence, legal basis,
   weaknesses.
B. RESPONDENT POSITION: stated response, supporting evidence, legal basis,
   weaknesses.
C. AGREED FACTS vs DISPUTED FACTS.
D. EVIDENCE GAPS: what could resolve the disputed facts.
E. STRENGTH COMPARISON: Claimant % vs Respondent % with reasoning.

Use generate_questions to suggest judicial questions for each party.

CONSTRAINTS:
- Analyze BOTH sides with equal depth and rigor.
- Note WEAKNESSES in both arguments. The Judge needs the full picture.
- Header: 'Internal Analysis for Judicial Review Only'.
```

**Tools:** `generate_questions`

**State I/O:**
- **Reads:** `evidence_analysis, extracted_facts, witnesses, legal_rules, precedents, domain, parties`
- **Writes:** `arguments (prosecution_args + defense_args + contested_issues for traffic, OR balanced_assessment for SCT, plus suggested_questions)`

**Guardrails:**
- Must analyze both sides with equal depth. Asymmetric analysis is a failure.
- Must note weaknesses in BOTH arguments, not just one side.
- Output must be headered "Internal Analysis for Judicial Review Only".
- Must not determine guilt/liability. That is the Judge's role.

---

### Agent 8: Deliberation Agent

| Merges | Classification | LLM | Tools |
|---|---|---|---|
| Standalone (core judicial reasoning brain) | LLM Reasoning | Yes | None |

**Scope:** The most critical agent in the pipeline. Synthesizes ALL upstream outputs into a single structured reasoning chain that traces the logical path from evidence to conclusion. Must remain independent for explainability and audit traceability.

**System Prompt:**

```
You are the Deliberation Agent for VerdictCouncil.
You are the judicial reasoning core of the system.

TASK: Produce a step-by-step reasoning chain from evidence to conclusion.

REASONING CHAIN:
1. ESTABLISHED FACTS: Facts supported by evidence, with confidence.
2. APPLICABLE LAW: Matched statutes with specific section references.
3. APPLICATION: For each legal element, does the evidence satisfy it?
4. ARGUMENT EVALUATION:
   Traffic: prosecution vs defense argument strength.
   SCT: claimant vs respondent position strength.
5. WITNESS ASSESSMENT: Credibility findings and their impact.
6. PRECEDENT ALIGNMENT: How do similar cases inform this analysis?
7. PRELIMINARY CONCLUSION: What does the chain suggest?
8. UNCERTAINTY FLAGS: Where is reasoning uncertain or dependent on
   factual determinations the Judge must make at hearing?

CONSTRAINTS:
- Every step MUST cite its source (which agent output, which evidence).
- Flag LOW-CONFIDENCE steps explicitly.
- This is a RECOMMENDATION, not a decision. The Judge decides.
- Do NOT present the conclusion with false certainty.
```

**State I/O:**
- **Reads:** `evidence_analysis, extracted_facts, witnesses, legal_rules, precedents, arguments`
- **Writes:** `deliberation (reasoning_chain with sourced steps, preliminary_conclusion, uncertainty_flags)`

**Guardrails:**
- Every step must cite the upstream agent and evidence that produced it.
- Must flag where reasoning depends on unresolved factual disputes.
- Must not present conclusions with false certainty. Uncertainty is valuable.

---

### Agent 9: Governance & Verdict Agent

| Merges | Classification | LLM | Tools |
|---|---|---|---|
| Fairness/Bias Check + Verdict Recommendation | LLM + Tools | Yes | 1 tool |

**Scope:** The final agent. First performs a fairness audit of the entire reasoning chain (bias check, unsupported claims, logical fallacies, balanced treatment). If the audit passes, generates a structured verdict recommendation. If critical bias is detected, the pipeline HALTS.

**System Prompt:**

```
You are the Governance & Verdict Agent for VerdictCouncil.
You are the final checkpoint before a recommendation reaches the Judge.

PHASE 1 - FAIRNESS AUDIT:
1. BALANCE: Were both parties' evidence weighted equally? Flag asymmetry.
2. UNSUPPORTED CLAIMS: Does reasoning rely on facts NOT in evidence?
3. LOGICAL FALLACIES: circular reasoning, false equivalences,
   confirmation bias, anchoring to early evidence.
4. DEMOGRAPHIC BIAS: reasoning influenced by race, gender, age,
   nationality, or socioeconomic status.
5. EVIDENCE COMPLETENESS: was any submitted evidence overlooked?
6. PRECEDENT CHERRY-PICKING: were contrary precedents acknowledged?

If ANY critical issue found: set recommendation to 'ESCALATE_HUMAN'
and STOP. Do NOT generate a verdict recommendation.

PHASE 2 - VERDICT RECOMMENDATION (only if audit passes):

FOR SCT: recommended_order (compensation/repair/dismiss), amount,
  legal_basis, reasoning_summary.
FOR TRAFFIC: recommended_verdict (guilty/not_guilty/reduced),
  sentence (fine, demerit points), sentencing_range from precedents.

ALWAYS INCLUDE:
- confidence_score: 0-100 (use confidence_calc tool)
- uncertainty_factors: what could change this recommendation
- alternative_outcomes: at least ONE other reasonable outcome
- fairness_report: summary of Phase 1 audit results

CONSTRAINTS:
- This is a RECOMMENDATION. State this clearly.
- Always present at least one alternative outcome.
- The Judge has FULL authority to accept, modify, or reject.
- Be AGGRESSIVE in flagging bias. False positives are acceptable.
```

**Tools:** `confidence_calc`

**State I/O:**
- **Reads:** `deliberation, evidence_analysis, arguments, precedents, legal_rules`
- **Writes:** `fairness_check (audit results), verdict_recommendation (outcome, reasoning, confidence, alternatives, sentencing)`

**Guardrails:**
- Must HALT pipeline if critical fairness issue detected. No partial verdicts.
- Must always present at least one alternative outcome.
- Must clearly label output as a recommendation, not a decision.
- Fairness audit must err on the side of flagging. False negatives are unacceptable.

---

## 4. Security & Prompt Injection Defenses

- **Plan-then-execute:** The orchestrator determines the execution plan BEFORE any agent processes untrusted document content. Once set, no agent can alter the plan.
- **Privilege separation:** Agents processing untrusted content (Case Processing, Evidence Analysis) have no write access to the execution plan or tool registry. They write only to their designated CaseState fields.
- **Content isolation:** Raw documents are never passed directly into system prompts. The `parse_document` tool extracts structured data, and only that structured output enters LLM context.
- **Output schema validation:** All agent outputs are validated against JSON schema before being written to shared state. Malformed outputs are rejected and logged.
- **SAM event broker audit:** All A2A messages flow through the Solace Event Broker, providing a complete, immutable audit trail of every agent interaction.
- **Governance Agent as final gate:** The Fairness/Bias audit in Agent 9 acts as a secondary injection check. If injected content influenced reasoning, unexplained logical jumps will be flagged.
- **Human-in-the-loop:** No recommendation reaches the judicial officer without passing the Governance Agent's audit. Critical issues halt the pipeline entirely.

---

# Part B: OpenAI Developer Platform Integration

---

## 5. OpenAI Model Strategy

### 5.1 Model Selection Rationale

VerdictCouncil uses a multi-model strategy, assigning each agent to the OpenAI model best suited for its reasoning demands. The key distinction is between **reasoning tasks** (deep analytical thinking, multi-step logic, legal interpretation) and **execution tasks** (structured extraction, classification, tool orchestration).

### 5.2 Model Tiers

VerdictCouncil resolves model names at runtime through environment variables; the canonical binding lives in `configs/shared_config.yaml` (YAML anchors `gpt54_nano_model`, `gpt5_mini_model`, `gpt5_model`, `gpt54_model`) and the matching defaults in `src/shared/config.py` (`openai_model_lightweight`, `openai_model_efficient_reasoning`, `openai_model_strong_reasoning`, `openai_model_frontier_reasoning`). See **[Part 4: Tech Stack §4.3](./architecture/04-tech-stack.md)** for the authoritative tier table.

| Tier alias (anchor) | Default model | Best For |
|---|---|---|
| `gpt54_nano_model` | `gpt-5.4-nano` | Structured extraction, jurisdiction checks, low-complexity routing |
| `gpt5_mini_model` | `gpt-5-mini` | Moderate reasoning with tool calls (e.g. witness credibility scoring) |
| `gpt5_model` | `gpt-5` | Long-context analysis over evidence, facts, and precedent retrieval |
| `gpt54_model` | `gpt-5.4` | Adversarial argument construction, judicial deliberation, fairness audit |

> **KEY PRINCIPLE:** reserve the frontier `gpt-5.4` tier for agents that must reason deeply (deliberation, argument construction, fairness audit). Use `gpt-5.4-nano` / `gpt-5-mini` for agents whose job is primarily extraction or structured decision-making.

### 5.3 Agent-to-Model Assignment

The agent-to-tier mapping below mirrors the assignments configured in `configs/agents/*.yaml` via the shared-config anchors.

| Agent | Tier alias | Default model | Mode | Rationale |
|---|---|---|---|---|
| **1. Case Processing** | `gpt54_nano_model` | `gpt-5.4-nano` | Execution | Structured extraction + deterministic rules. Minimal reasoning. |
| **2. Complexity & Routing** | `gpt54_nano_model` | `gpt-5.4-nano` | Execution | Low-token triage decision over structured case metadata. |
| **3. Evidence Analysis** | `gpt5_model` | `gpt-5` | Execution + Tools | Long-context pass over evidence bundles; tool-heavy. |
| **4. Fact Reconstruction** | `gpt5_model` | `gpt-5` | Execution + Tools | Cross-document timeline construction; benefits from long context. |
| **5. Witness Analysis** | `gpt5_mini_model` | `gpt-5-mini` | Reasoning + Tools | Credibility assessment needs genuine reasoning at moderate cost. |
| **6. Legal Knowledge** | `gpt5_model` | `gpt-5` | Execution + Tools | Primarily tool-driven: `file_search` + PAIR precedent API. |
| **7. Argument Construction** | `gpt54_model` | `gpt-5.4` | Deep Reasoning | Constructs adversarial arguments and identifies weaknesses. |
| **8. Deliberation** | `gpt54_model` | `gpt-5.4` | Deep Reasoning | The judicial reasoning chain — highest-stakes step. |
| **9. Governance & Verdict** | `gpt54_model` | `gpt-5.4` | Deep Reasoning | Fairness audit must detect subtle biases and logical fallacies. |

**Cost note:** actual $-per-case is a function of volumetrics (documents, precedents, fairness-audit passes) and current OpenAI pricing. See `tests/eval/` for the cost-tracking harness rather than relying on estimates embedded here.

### 5.4 Reasoning Effort Configuration

Where the selected model supports a `reasoning_effort` parameter, we bias as follows:

| Agent | Tier | Effort | Rationale |
|---|---|---|---|
| 2. Complexity & Routing | `gpt-5.4-nano` | `low` | Simple triage decision. |
| 5. Witness Analysis | `gpt-5-mini` | `medium` | Credibility assessment benefits from moderate thinking. |
| 7. Argument Construction | `gpt-5.4` | `high` | Must thoroughly explore both sides' strongest arguments. |
| 8. Deliberation | `gpt-5.4` | `high` | Core reasoning chain; maximum thinking time for thorough analysis. |
| 9. Governance & Verdict | `gpt-5.4` | `medium` | Fairness audit needs solid reasoning; verdict follows a structured format. |

---

## 6. RAG Pipeline: OpenAI Vector Stores

### 6.1 Architecture Overview

VerdictCouncil's legal knowledge retrieval is powered by **OpenAI Vector Stores** with the `file_search` tool via the Responses API. This provides a fully managed RAG pipeline: OpenAI handles parsing, chunking, embedding (via `text-embedding-3-large`), indexing, and hybrid retrieval (semantic + keyword search).

**Why OpenAI Vector Stores (vs. self-hosted):**
- Zero infrastructure: no Pinecone, Weaviate, or Chroma to deploy and maintain.
- Automatic chunking and embedding using `text-embedding-3-large`.
- Hybrid retrieval: combines semantic (meaning-based) and keyword search for better recall.
- Native integration with the Responses API: `file_search` is a built-in tool.
- Metadata filtering: filter results by jurisdiction, document_type, domain, etc.
- Cost-effective: $0.10/GB/day storage + $2.50/1000 search calls.

### 6.2 Vector Store Design

Two Vector Stores, one per legal domain. Each contains curated, authoritative legal sources only.

**Vector Store: `vs_sct` (Small Claims Tribunal)**

```python
vector_store = client.vector_stores.create(
    name='verdictcouncil-sct-knowledge',
    metadata={
        'domain': 'small_claims',
        'jurisdiction': 'singapore',
        'version': '2026-03'
    }
)
```

Contents: Small Claims Tribunals Act (Cap 308), Small Claims Tribunals Rules, Consumer Protection (Fair Trading) Act, Sale of Goods Act, Supply of Services (Implied Terms) Act, curated SCT precedent case summaries (100–200 cases).

**Vector Store: `vs_traffic` (Traffic Violations)**

```python
vector_store = client.vector_stores.create(
    name='verdictcouncil-traffic-knowledge',
    metadata={
        'domain': 'traffic_violation',
        'jurisdiction': 'singapore',
        'version': '2026-03'
    }
)
```

Contents: Road Traffic Act (Cap 276), Road Traffic Rules, Motor Vehicles (Third-Party Risks and Compensation) Act, Traffic Police enforcement guidelines, sentencing guidelines, curated traffic case precedents (100–200 cases).

### 6.3 File Upload with Metadata Tagging

Each file is tagged with metadata for filtered search:

```python
# Upload a statute with metadata
file = client.files.create(
    file=open('road_traffic_act.pdf', 'rb'),
    purpose='assistants'
)

client.vector_stores.files.create(
    vector_store_id='vs_traffic',
    file_id=file.id,
    attributes={
        'document_type': 'statute',
        'act_name': 'Road Traffic Act',
        'jurisdiction': 'singapore',
        'last_amended': '2024-01-15'
    }
)

# Upload a precedent case with metadata
client.vector_stores.files.create(
    vector_store_id='vs_traffic',
    file_id=precedent_file.id,
    attributes={
        'document_type': 'precedent',
        'case_citation': 'PP v Tan [2023] SGDC 45',
        'offence_type': 'red_light_violation',
        'outcome': 'fine_1200_8_demerit',
        'year': '2023'
    }
)
```

### 6.4 RAG Retrieval Flow

1. Agent formulates a semantic query from case facts.
2. Agent calls `file_search` with the appropriate `vector_store_id` and metadata filters.
3. OpenAI performs hybrid search (semantic + keyword) against the vector store.
4. Top-k results are returned with source citations (file name, page, chunk).
5. Agent processes results, maps them to case facts, and writes to `legal_rules` and `precedents`.

```python
# Legal Knowledge Agent's RAG call via Responses API
response = client.responses.create(
    model='gpt-5',
    input=[{
        'role': 'user',
        'content': f'Find relevant statutes and precedents for: {query}'
    }],
    tools=[{
        'type': 'file_search',
        'vector_store_ids': [vector_store_id],
        'filters': {
            'type': 'and',
            'filters': [
                {'type': 'eq', 'key': 'jurisdiction', 'value': 'singapore'},
                {'type': 'eq', 'key': 'document_type', 'value': 'statute'}
            ]
        }
    }],
    instructions=LEGAL_KNOWLEDGE_AGENT_SYSTEM_PROMPT
)
```

---

## 7. OpenAI Function-Calling Tool Definitions

All custom tools are defined using OpenAI's function-calling JSON schema format. These definitions are passed in the `tools` parameter of the Responses API call.

### 7.1 parse_document

**Used by:** Agent 1 (Case Processing), Agent 3 (Evidence Analysis), Agent 4 (Fact Reconstruction)

```json
{
  "type": "function",
  "function": {
    "name": "parse_document",
    "description": "Extract text content, metadata, and structural elements from an uploaded case file. Supports PDF, images, and text files. Returns structured content with page numbers and paragraph references for citation.",
    "parameters": {
      "type": "object",
      "properties": {
        "file_id": {
          "type": "string",
          "description": "OpenAI file ID of the uploaded document"
        },
        "extract_tables": {
          "type": "boolean",
          "default": false,
          "description": "Whether to extract structured table data"
        },
        "ocr_enabled": {
          "type": "boolean",
          "default": true,
          "description": "Enable OCR for scanned documents/images"
        }
      },
      "required": ["file_id"]
    }
  }
}
```

### 7.2 cross_reference

**Used by:** Agent 3 (Evidence Analysis), Agent 5 (Witness Analysis)

```json
{
  "type": "function",
  "function": {
    "name": "cross_reference",
    "description": "Compare document segments from different sources to identify contradictions, inconsistencies, or corroborations. Returns a list of findings with severity ratings.",
    "parameters": {
      "type": "object",
      "properties": {
        "segments": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "source_doc": { "type": "string" },
              "page": { "type": "integer" },
              "text": { "type": "string" },
              "party": { "type": "string" }
            }
          },
          "minItems": 2,
          "description": "Two or more document segments to compare"
        },
        "check_type": {
          "type": "string",
          "enum": ["contradiction", "corroboration", "both"],
          "default": "both"
        }
      },
      "required": ["segments"]
    }
  }
}
```

### 7.3 timeline_construct

**Used by:** Agent 4 (Fact Reconstruction)

```json
{
  "type": "function",
  "function": {
    "name": "timeline_construct",
    "description": "Build a chronological timeline from extracted events. Resolves date format variations, orders events, and flags temporal conflicts.",
    "parameters": {
      "type": "object",
      "properties": {
        "events": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "date": { "type": "string" },
              "time": { "type": "string" },
              "description": { "type": "string" },
              "source_doc": { "type": "string" },
              "parties_involved": {
                "type": "array",
                "items": { "type": "string" }
              }
            }
          }
        }
      },
      "required": ["events"]
    }
  }
}
```

### 7.4 generate_questions

**Used by:** Agent 5 (Witness Analysis), Agent 7 (Argument Construction)

```json
{
  "type": "function",
  "function": {
    "name": "generate_questions",
    "description": "Generate probing judicial questions that target weaknesses in a legal argument. Questions are tagged by type and linked to specific evidence or argument points.",
    "parameters": {
      "type": "object",
      "properties": {
        "argument_summary": {
          "type": "string",
          "description": "Summary of the argument to probe"
        },
        "weaknesses": {
          "type": "array",
          "items": { "type": "string" },
          "description": "Identified weaknesses to target"
        },
        "question_types": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": [
              "factual_clarification",
              "evidence_gap",
              "credibility_probe",
              "legal_interpretation"
            ]
          },
          "default": ["factual_clarification", "evidence_gap"]
        },
        "max_questions": {
          "type": "integer",
          "default": 5,
          "maximum": 10
        }
      },
      "required": ["argument_summary", "weaknesses"]
    }
  }
}
```

### 7.5 confidence_calc

**Used by:** Agent 9 (Governance & Verdict)

```json
{
  "type": "function",
  "function": {
    "name": "confidence_calc",
    "description": "Calculate a confidence score (0-100) for the verdict recommendation based on weighted evidence strength, legal rule relevance, precedent alignment, and witness credibility scores.",
    "parameters": {
      "type": "object",
      "properties": {
        "evidence_scores": {
          "type": "array",
          "items": { "type": "number" },
          "description": "List of evidence strength scores (0-100)"
        },
        "rule_relevance_scores": {
          "type": "array",
          "items": { "type": "number" },
          "description": "Relevance scores for matched legal rules"
        },
        "precedent_similarity_scores": {
          "type": "array",
          "items": { "type": "number" }
        },
        "witness_credibility_scores": {
          "type": "array",
          "items": { "type": "number" }
        },
        "weights": {
          "type": "object",
          "properties": {
            "evidence": { "type": "number", "default": 0.35 },
            "rules": { "type": "number", "default": 0.25 },
            "precedents": { "type": "number", "default": 0.25 },
            "witnesses": { "type": "number", "default": 0.15 }
          }
        }
      },
      "required": ["evidence_scores", "rule_relevance_scores"]
    }
  }
}
```

### 7.6 file_search (Built-in OpenAI Tool)

**Used by:** Agent 6 (Legal Knowledge)

This is a built-in OpenAI tool — no custom function definition needed. Configured by passing `vector_store_ids` and optional metadata filters:

```json
{
  "type": "file_search",
  "vector_store_ids": ["vs_sct"],
  "max_num_results": 10,
  "filters": {
    "type": "and",
    "filters": [
      { "type": "eq", "key": "document_type", "value": "statute" },
      { "type": "eq", "key": "jurisdiction", "value": "singapore" }
    ]
  }
}
```

---

## 8. End-to-End Integration: SAM + OpenAI

### 8.1 Agent Execution Pattern

Each SAM agent wraps an OpenAI Responses API call:

```python
def handle_message(self, case_state: dict) -> dict:
    response = openai_client.responses.create(
        model=self.config['model'],              # e.g., 'gpt-5.4'
        instructions=self.config['system_prompt'],
        input=[{
            'role': 'user',
            'content': json.dumps(case_state)
        }],
        tools=self.config['tools'],               # function defs + file_search
        reasoning={
            'effort': self.config.get('reasoning_effort', 'medium')
        },
        temperature=self.config.get('temperature', 0.1)
    )
    
    result = self.process_tool_calls(response)
    updated_state = self.write_to_state(case_state, result)
    self.publish(self.config['output_topic'], updated_state)
    return updated_state
```

### 8.2 Complete Agent Configuration Summary

| Agent | Model | Custom Tools | Built-in Tools | Effort | Event Topic Flow |
|---|---|---|---|---|---|
| **1. Case Processing** | gpt-5.4-nano | parse_document | — | N/A | intake → complexity |
| **2. Complexity & Routing** | gpt-5.4-nano | — | — | low | complexity → evidence |
| **3. Evidence Analysis** | gpt-5 | parse_document, cross_reference | — | N/A | evidence → facts |
| **4. Fact Reconstruction** | gpt-5 | timeline_construct, cross_reference | — | N/A | facts → witnesses |
| **5. Witness Analysis** | gpt-5-mini | cross_reference, generate_questions | — | medium | witnesses → legal |
| **6. Legal Knowledge** | gpt-5 | — | file_search (vs_sct / vs_traffic) | N/A | legal → arguments |
| **7. Argument Construction** | gpt-5.4 | generate_questions | — | high | arguments → deliberation |
| **8. Deliberation** | gpt-5.4 | — | — | high | deliberation → governance |
| **9. Governance & Verdict** | gpt-5.4 | confidence_calc | — | medium | verdict → judge_ui |

### 8.3 SAM Topic Hierarchy

```
verdictcouncil/
  pipeline/
    >{case_id}/
      intake          # Case Processing → Complexity
      complexity      # Complexity → Evidence
      evidence        # Evidence → Facts
      facts           # Facts → Witnesses
      witnesses       # Witnesses → Legal Knowledge
      legal           # Legal → Arguments
      arguments       # Arguments → Deliberation
      deliberation    # Deliberation → Governance
      verdict         # Governance → Judge UI
  gateway/
    web/              # Web UI gateway for judicial officer
    api/              # REST API gateway for CJTS integration
```

### 8.4 Cost Optimization Notes

- **Prompt caching:** OpenAI caches repeated input tokens. System prompts are identical across calls for a given agent, so repeat cases benefit from the cached-input discount on the `gpt-5` / `gpt-5.4` tiers.
- **Structured outputs:** Agents 1, 3, 4, and 6 use `response_format: { type: 'json_schema', ... }` to enforce structured output, reducing retry costs from malformed responses.
- **Context window management:** Only relevant CaseState fields are passed to each agent (not the entire state), minimizing input token consumption.
- **Vector Store costs:** At ~300 legal documents totaling ~50MB, storage costs are approximately $0.005/day. Search calls at $2.50/1000 are negligible at project scale.
- **Model fallback:** If the `gpt-5.4` tier is unavailable, Agents 7, 8, and 9 fall back to `gpt-5` with `reasoning_effort: 'high'` — acceptable for low-complexity cases at reduced cost. The fallback is controlled by `OPENAI_MODEL_FRONTIER_REASONING` / `OPENAI_MODEL_STRONG_REASONING` env vars resolved in `configs/shared_config.yaml`.