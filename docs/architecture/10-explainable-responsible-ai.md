# Part 10: Explainable & Responsible AI Practices

VerdictCouncil is a decision-support system for a **judicial** context — the highest-stakes use of AI this course will touch. Every design decision in the backend is filtered through an Explainable & Responsible AI (ERAI) lens. This document maps our practices to each life-cycle stage and to the IMDA Model AI Governance Framework.

> **Live vs target.** This document describes the canonical architecture that underwrites our ERAI posture (per-agent container topology, NetworkPolicy, HMAC-signed inter-service calls, etc.). The MVP ship state is mono-process; see the **Implementation Status** callouts in [Part 2 §2.2](02-system-architecture.md#22-orchestration-platform), [Part 6 §6.6](06-cicd-pipeline.md#66-kubernetes-manifests), and [Part 8 §8.3.3](08-infrastructure-setup.md#833-node-pool-sizing) for the current gap between canonical and deployed. Controls marked below that depend on the per-agent topology (privilege separation via NetworkPolicy, HMAC-signed dispatch, per-agent NetworkPolicy lateral-movement containment) are in the target tier; logical privilege separation is preserved even in mono-process mode via the `_merge_case` reducer and per-agent schema validation, but the network-layer enforcement is target work.

---

## 10.1 Core commitments (non-negotiable)

1. **No AI verdict.** The system never emits a binding verdict. The `hearing-analysis` agent produces a *preliminary conclusion*; the `hearing-governance` agent audits for fairness; the judge records the actual decision via `POST /cases/{id}/decision`. This is enforced in code — no endpoint exists that mutates `case.judicial_decision` without a judge's session cookie.
2. **Every inference is reproducible.** Every node invocation writes an `AuditEntry` to `CaseState.audit_log` containing system prompt, user message, tool calls, raw LLM response, model, token usage, timestamp. The entry is checkpointed to Postgres immediately, mirrored to MLflow, and projected into the `audit_logs` relational table for fast query.
3. **Every inference is revocable.** The checkpointer preserves `GraphState` after every node. The judge (via what-if mode) or an admin (via manual escalation) can replay from any point with modified inputs without losing the original run.
4. **Judge stays in the loop at four gates.** Pipeline pauses after Gate 1 (intake), Gate 2 (evidence), Gate 3 (hearing analysis), Gate 4 (fairness audit). Advancement is a judge action, not an automatic flow.

---

## 10.2 Alignment across development stages

### 10.2.1 Design

- **Agents have narrow, single-purpose responsibilities** (see [Part 3 §3.4](03-agent-configurations.md#34-agent-reference)). No agent has license to conclude what another owns — e.g. `legal-knowledge` never writes `hearing_analyses`, `argument-construction` never writes `evidence_analysis`.
- **Per-agent output is schema-bound** (`src/pipeline/agent_schemas.py`). `hearing-governance` uses OpenAI strict-schema mode so no free-form output can smuggle unvetted claims into the fairness audit.
- **Topology is fixed at deploy time** in `src/pipeline/graph/builder.py`. User content cannot alter routing. Conditional edges evaluate typed state fields, not LLM output.

### 10.2.2 Prompting

- Prompts live in `src/pipeline/graph/prompts.py`, versioned with code. Every change is PR-reviewed; the diff shows the exact prompt delta that shipped.
- Prompts explicitly instruct agents to **cite sources**, **refuse to invent**, and **mark uncertainty**. Agents that produce evaluations (`witness-analysis`, `hearing-analysis`) must attach confidence scores and uncertainty flags.
- Document content is wrapped in `<user_document>...</user_document>` delimiters at the user-message boundary — never interpolated into the system prompt (see [Part 2 §2.7](02-system-architecture.md#27-security-and-prompt-injection-defenses) "Content Isolation").

### 10.2.3 Deployment

- **Per-agent containers with blast-radius limits** (see [Part 6 §6.6](06-cicd-pipeline.md#66-kubernetes-manifests)). A compromised `evidence-analysis` pod cannot reach `hearing-governance` — they are separate Services, gated by NetworkPolicy, and can't discover each other.
- **Deployments are trunk-based with gitflow in the backend** (see `CLAUDE.md`). Changes to prompts, schemas, or agent logic land on `main` only after CI (lint + unit + SAST + SCA + DAST + docker build) passes.
- **Secrets are rendered at deploy time** (see [Part 6 §6.4–§6.5](06-cicd-pipeline.md#64-staging-deploy-workflow-live)) — nothing sensitive lives in the image or the repo.

### 10.2.4 Monitoring + feedback

- MLflow tracks every agent and pipeline run (`src/pipeline/observability.py`). Drift in model behaviour shows up as a drift in the MLflow experiment, not silently.
- Prometheus scrapes `/metrics` on the API and (target) each agent Service; Grafana dashboards the per-agent p95 latency, token usage, and tool-call error rates.
- The `stuck-case-watchdog` CronJob (every 5 min) surfaces cases stalled > 30 min so a judge's view never silently waits forever.

---

## 10.3 Fairness and bias mitigation

### 10.3.1 Design-level

- **Two competing arguments, not one.** `argument-construction` writes *both* sides' strongest case with symmetric weight budgets. Prompts forbid the agent from "picking a winner" inside Gate 3.
- **Demographic bias flag.** `hearing-governance` explicitly audits for demographic factors (gender, race, age, socio-economic status) surfacing in reasoning chains. Any hit sets `fairness_check.critical_issues_found = true` and halts the pipeline.
- **Precedent cherry-picking check.** Same agent audits whether precedents cited favour one side disproportionately without distinguishing the facts.
- **Balance check.** Symmetric attention across claimant/respondent (SCT) or prosecution/defence (Traffic). Imbalance > configured threshold trips a fairness flag.

### 10.3.2 Data-level

- **Curated domain KB** supplements PAIR (higher-court decisions only). Admins manually review new domain documents before they become searchable, preventing low-quality or biased material from entering retrieval (see [US-031](01-user-stories.md#us-031--manage-the-knowledge-base-and-refresh-vector-stores)).
- **Evaluation fixtures** ([Part 7 Appendix D](07-contestable-judgment-mode.md#appendix-d-evaluation-framework)) include three cases spanning Traffic (straightforward guilty), Traffic (contested), and SCT (balanced dispute). The "What-If Test" columns verify that altering the dispositive fact correctly flips the conclusion — a direct test of whether the system over-weights certain fact classes.

### 10.3.3 Process-level

- **Judges can contest conclusions** via what-if ([US-021](01-user-stories.md#us-021--run-a-what-if-scenario)). If toggling a fact flips the conclusion with a single perturbation, the judge sees that fragility and can escalate.
- **Stability scores** quantify robustness — a "highly sensitive" score (< 60/100) explicitly tells the judge the AI's preliminary view hinges on a narrow factual read.

---

## 10.4 Explainability

### 10.4.1 What the judge can inspect

For every case, the judge can drill into:

1. **Full audit trail** ([US-026](01-user-stories.md#us-026--view-the-full-audit-trail)) — per-agent inputs, prompts, outputs, tool calls, model, token usage.
2. **Source anchors** — every fact and evidence item cites its source document + page; the judge can jump directly into the parsed page ([US-008](01-user-stories.md#us-008--drill-from-a-factevidence-entry-to-the-source-document)).
3. **Per-agent reasoning chain** — `hearing-analysis` produces a stepwise `reasoning_chain` the judge can read like a bench memo.
4. **Uncertainty flags** — every hearing analysis carries `uncertainty_flags[]` marking claims the system is not confident about.
5. **Fairness audit findings** ([US-023](01-user-stories.md#us-023--review-the-fairness--bias-audit)) — if the governance agent raised any concerns, the judge sees the specific reasoning step the issue attaches to.

### 10.4.2 Mechanisms

| Mechanism | Where | What it gives the judge |
|---|---|---|
| `CaseState.audit_log` | Postgres `audit_logs` + checkpoint | Immutable record of every LLM call on the case |
| MLflow | `mlflow_run_ids` per agent | Pinnable, comparable runs across what-if variants |
| Source-ref metadata | `facts[].source_refs`, `evidence_items[].doc_refs` | Direct jump from a claim to the underlying document page |
| Structured reasoning chain | `HearingAnalysis.reasoning_chain` (JSONB) | Stepwise legal logic the judge can audit |
| Uncertainty flags | `HearingAnalysis.uncertainty_flags` | Explicit places the judge should spend extra attention |
| Confidence score | `HearingAnalysis.confidence_score` | One-number sanity check (see calibration disclaimer below) |
| Diff view | `DiffEngine` on what-if | Side-by-side comparison against the baseline run |

### 10.4.3 Calibration disclaimer (carried through to UI)

All numerical scores (credibility, confidence, stability) are **relative indicators** produced by LLM reasoning, not statistically calibrated measurements. They are displayed with a disclaimer so judges do not treat them as probabilities.

---

## 10.5 IMDA Model AI Governance Framework alignment

Mapping to the IMDA framework's four organising principles:

| Principle | How we address it | Where |
|---|---|---|
| **Accountability** | A named judge must sign every decision; admin actions are logged to `admin_events`; per-agent MLflow runs link every inference to a deployed image tag and prompt version | [US-025](01-user-stories.md#us-025--record-the-judicial-decision), `src/models/admin_event.py`, [Part 6 §6.5](06-cicd-pipeline.md#65-production-deploy-workflow-live) |
| **Human Oversight** | Four mandatory review gates with an HITL pause at each. The system is advisory-only; no endpoint exists that emits a binding outcome without judge action | [Part 2 §2.7](02-system-architecture.md#27-security-and-prompt-injection-defenses) "Human-in-the-Loop (4-Gate HITL)" |
| **Operations Management** | CI/CD gates (lint, unit, SAST, SCA, DAST), K8s Deployment rollout, stuck-case watchdog CronJob, MLflow drift tracking, per-run checkpoints | [Part 6](06-cicd-pipeline.md), [Part 8 §8.12](08-infrastructure-setup.md#812-monitoring--alerting) |
| **Stakeholder Interaction** | Judge gets full reasoning chain + audit trail + source anchors + what-if + stability scores; admin gets KB controls + cost alerts + system health; UI shows calibration disclaimer next to every AI-produced score | [Part 1 §1.3–§1.10](01-user-stories.md#13-evidence--facts-judge) |

---

## 10.6 How explainability ties back to the architecture

The per-agent container topology ([Part 2 §2.2](02-system-architecture.md#22-orchestration-platform)) is not ERAI ornamentation — it's load-bearing:

- **Privilege separation.** Each agent can only read/write its designated `CaseState` fields. A compromised agent cannot rewrite the audit log of another.
- **Tamper resistance.** Three independent observation channels (audit_log, MLflow, structured stdout) must agree; the Postgres checkpoint is authoritative if they diverge. You cannot hide a bad inference by editing one record.
- **Halt-on-failure.** Schema validation failure, retry-cap exhaustion, and fairness-audit findings all route the pipeline to `terminal` with a halt reason attached. There is no "silent degradation" path.
- **Orchestrator-only routing.** No agent decides the next hop. Even if `case-processing` is compromised, it cannot skip `hearing-governance`.

---
