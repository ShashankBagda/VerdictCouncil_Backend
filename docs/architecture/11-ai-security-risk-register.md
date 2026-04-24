# Part 11: AI Security Risk Register

Structured register of AI-specific and system-level security risks, their mitigations, and who owns each control. Likelihood × impact scoring is 1–5 (1 = very low, 5 = very high); residual = after the listed mitigations are in place.

| # | Risk | Category | Likelihood | Impact | Risk | Mitigation | Control Owner |
|---|---|---|---|---|---|---|---|
| 1 | **Prompt injection via uploaded documents** — adversary embeds instructions in a case document intending to steer an agent's reasoning. | Input Integrity | 4 | 4 | **16** | Two-layer `llm-guard` sanitisation at ingest (regex fast-path + DeBERTa-v3 classifier) in `src/shared/sanitization.py`; `pre_run_guardrail` node re-applies per run; document content is user-message only, wrapped in `<user_document>` delimiters, never interpolated into system prompts. Residual: **6** (3×2). | Backend team (`evidence-analysis`, `case-processing` owners) |
| 2 | **Indirect injection via curated domain KB** — malicious content in a domain-guidance document surfaces inside `search_domain_guidance` results and steers `legal-knowledge`. | Input Integrity | 3 | 4 | **12** | Admin-gated upload (US-031); `CLASSIFIER_SANITIZER_ENABLED=true` runs DeBERTa-v3 on every KB ingest page; `DOMAIN_UPLOADS_ENABLED` kill-switch for incident response. Residual: **4** (2×2). | Admin persona + DevSecOps |
| 3 | **Data exfiltration via LLM tool calls** — an agent is coaxed into emitting sensitive case data to an attacker-controlled URL. | Confidentiality | 2 | 5 | **10** | Agents have no HTTP tool bindings except `search_precedents` (fixed PAIR endpoint) and `search_domain_guidance` (OpenAI vector store). No generic web-fetch tool exists. OpenAI Files API is the only outbound file channel. Residual: **4** (2×2). | Tool owner (`src/tools/`) + code review |
| 4 | **Model hallucination of statutes or precedents** — `legal-knowledge` cites a non-existent section or case. | Reliability | 3 | 5 | **15** | Every citation flows through retrieval tools (PAIR or the curated vector store); prompt forbids uncited legal claims; schema validation rejects results without `source: curated|live_search`; [Appendix D hallucination checklist](07-contestable-judgment-mode.md#d7-hallucination-detection-checklist) is part of the evaluation run before every release. Residual: **6** (2×3). | Backend team + Evaluation |
| 5 | **Adversarial input robustness** — unusual/benign-looking document formatting crashes `parse_document` or causes the agent to refuse. | Robustness | 3 | 3 | **9** | `parse_document` returns structured output with defined failure modes; Orchestrator retries failed tool calls with exponential backoff up to a cap; `_merge_case` reducer safely handles partial/empty outputs; unit tests cover malformed PDFs, oversized images, and non-UTF8 text. Residual: **3** (2×2). | Tool owner |
| 6 | **Insecure tool use / authorisation escalation** — an agent calls a tool with parameters outside its privilege scope. | Authorization | 2 | 4 | **8** | Tools are constructed per-run via `make_tools(case_state)` and close over the active `case_id` / `domain_vector_store_id` — agents cannot pass arbitrary targeting parameters. PAIR client enforces its own rate limit + circuit breaker (`src/shared/circuit_breaker.py`). Residual: **2** (1×2). | Tool owner |
| 7 | **Cross-agent lateral movement** — a compromised agent reaches peer agents' `/invoke` endpoints to poison shared state. | Lateral Movement | 2 | 5 | **10** | Agents are ClusterIP-only Services gated by NetworkPolicy: only the Orchestrator pod can reach `/invoke`. All Orchestrator→agent requests are HMAC-signed (`X-VC-Signature` over `run_id + agent_name + body_sha256`) with a per-deployment secret; agents reject unsigned requests. Residual: **2** (1×2). | Platform (see [Part 2 §2.7](02-system-architecture.md#inter-service-auth)) |
| 8 | **Checkpoint / audit-log tampering** — an attacker with Postgres read/write access alters an `AuditEntry` to cover an incorrect inference. | Integrity | 2 | 5 | **10** | Three independent observation channels (Postgres `audit_logs`, LangGraph `checkpoints`, MLflow experiment) must agree; the checkpoint row is authoritative if they diverge. DB credentials are tightly scoped (app user has no DDL); backup snapshots on DO Managed Postgres. Target: cryptographic signatures on `AuditEntry` rows. Residual: **4** (2×2). | Platform + DBA |
| 9 | **PAIR circuit-breaker bypass / denial-of-service** — attacker triggers many PAIR searches via repeated case submissions to exhaust PAIR rate budget. | Availability | 3 | 3 | **9** | Redis-backed rate limit inside `search_precedents` (max 2 req/s); per-user rate limit on `POST /cases`; circuit breaker opens after `PAIR_CIRCUIT_BREAKER_THRESHOLD` failures and falls back to the curated vector store. Residual: **3** (2×2). | Backend team + Admin (rate-limit config) |
| 10 | **Session replay / stolen cookie** — attacker replays a `vc_token` cookie to impersonate a judge. | Authentication | 2 | 5 | **10** | `vc_token` is `httpOnly`, `Secure` (in prod), `SameSite=Lax`; server-side session hash means revocation works instantly; HS256 signing with a rotated secret; password reset forces logout. Target: binding tokens to client IP + user-agent. Residual: **4** (2×2). | Backend team (`src/api/deps.py`) |
| 11 | **CSRF against judge decision endpoint** — attacker lures a logged-in judge into submitting a forged decision. | Input Integrity | 2 | 5 | **10** | `SameSite=Lax` cookie is necessary but not sufficient for a state-changing POST; `POST /cases/{id}/decision` requires an `X-CSRF-Token` header matched against the session. Target: double-submit token via a framework middleware instead of ad-hoc. Residual: **4** (2×2). | Backend team |
| 12 | **Secrets in image / logs** — an OpenAI key or HMAC secret leaks via a stdout log or an image layer. | Confidentiality | 2 | 5 | **10** | `bandit` + `pip-audit` in CI flag credential patterns; secrets are only ever loaded via K8s `envFrom: secretRef`; structured-log formatter redacts keys matching well-known patterns; image scans via DOCR. Residual: **2** (1×2). | DevSecOps |
| 13 | **Supply-chain attack on a Python dependency** — malicious update to `langchain-openai`, `llm-guard`, or another pinned dep. | Supply Chain | 2 | 5 | **10** | Versions pinned in `pyproject.toml`; `pip-audit` + `safety` + `cyclonedx-bom` SBOM runs in CI on every commit; Dependabot alerts for critical vulnerabilities. Target: SBOM attestation on release images. Residual: **4** (2×2). | DevSecOps |
| 14 | **Model-tier downgrade attack** — attacker flips the `OPENAI_MODEL_FRONTIER_REASONING` env value to a cheap model to degrade governance. | Config Tampering | 1 | 5 | **5** | Model tier env vars live in the K8s `verdictcouncil-secrets`; only DevSecOps pipelines can write. Start-up logs the resolved model per tier so drift is visible; MLflow run captures the model used per inference. Residual: **2** (1×2). | DevSecOps |
| 15 | **Excessive token cost (runaway case)** — a malformed case triggers unbounded tool-call loops and burns budget. | Availability / Cost | 3 | 3 | **9** | Per-agent tool-call cap enforced by `_run_agent_node`; arq `job_timeout=900s`; per-agent HTTP timeout (180s default, 300s frontier); admin-configured cost ceiling halts new runs when a daily or monthly threshold is crossed (US-034). Residual: **3** (2×2). | Backend team + Admin |

---

## 11.1 Scoring methodology

- **Likelihood (1–5):** 1 = attacker needs privileged insider access, 5 = any external user can attempt trivially.
- **Impact (1–5):** 1 = cosmetic, 5 = affects a judicial decision or leaks sensitive case data.
- **Risk score:** likelihood × impact.
- **Residual:** same formula after the listed mitigations are fully deployed. Residual > 6 is reviewed quarterly.

## 11.2 Control ownership

| Owner | Scope |
|---|---|
| **Backend team** | Agent code, tool implementations, `src/api`, rate limits, retry logic |
| **Platform** | K8s manifests, NetworkPolicy, secrets flow, HMAC rotation, DB credentials |
| **DevSecOps** | CI gates (SAST/SCA/DAST), SBOM, release image scanning, secret rotation |
| **Tool owner** | Individual tool modules in `src/tools/`; responsible for sandboxing and contract validation |
| **Admin persona** | KB moderation, rate-limit config, cost ceilings, incident kill-switches |

## 11.3 Quarterly review

The register is reviewed every quarter or after any security incident, whichever comes first. Any new agent, tool, or external integration must land with at least one row in this register before the feature is merged — this is enforced by a PR-template checklist and by CODEOWNERS on `docs/architecture/11-ai-security-risk-register.md`.

---
