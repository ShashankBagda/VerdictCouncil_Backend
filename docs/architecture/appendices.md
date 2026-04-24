# Appendices

## Appendix A: Cost Model

### Per-Case Cost Breakdown

Per-agent tokens are informed estimates; your mileage will vary with case size. Prices assume April 2026 OpenAI list prices for the referenced models.

| # | Agent | Model | Est. Input Tokens | Est. Output Tokens | Est. Cost (USD) |
|---|---|---|---|---|---|
| 1 | `case-processing` | gpt-5.4-nano | ~5,000 | ~2,000 | $0.004 |
| 2 | `complexity-routing` | gpt-5.4-nano | ~2,000 | ~500 | $0.001 |
| 3 | `evidence-analysis` | gpt-5 | ~15,000 | ~5,000 | $0.069 |
| 4 | `fact-reconstruction` | gpt-5 | ~10,000 | ~3,000 | $0.043 |
| 5 | `witness-analysis` | gpt-5-mini | ~8,000 | ~3,000 | $0.008 |
| 6 | `legal-knowledge` | gpt-5 | ~10,000 | ~5,000 | $0.063 |
| 7 | `argument-construction` | gpt-5.4 | ~12,000 | ~5,000 | $0.105 |
| 8 | `hearing-analysis` | gpt-5.4 | ~15,000 | ~5,000 | $0.113 |
| 9 | `hearing-governance` | gpt-5.4 | ~10,000 | ~3,000 | $0.070 |
| | **Total LLM per case** | | **~87,000** | **~31,500** | **$0.40 – $0.55** |

Add ~$2.88 per what-if perturbation that re-enters at `argument-construction`, and ~2× that for `evidence_exclusion` (re-runs the full Gate 2 in parallel). A stability score at N=5 costs ~$14.40 for fact/credibility/legal modifications.

### Infrastructure Costs (DigitalOcean)

| Item | Staging | Production | Notes |
|---|---|---|---|
| DOKS nodes | $96/mo (2× s-4vcpu-8gb) | $144/mo (3× s-4vcpu-8gb) | Control plane is free; auto-scale adds $48/node |
| DO Load Balancer | $12/mo | $12/mo | Auto-provisioned by NGINX ingress controller |
| Managed PostgreSQL 16 | $30/mo (s-1vcpu-2gb) | $60/mo (s-2vcpu-4gb) | Daily backups included; +$60 for HA standby |
| Managed Redis 7 | $15/mo (s-1vcpu-1gb) | $15/mo (s-1vcpu-2gb) | TLS included; eviction policy: allkeys-lru |
| DOCR (Professional) | — | $12/mo | Shared registry, 50 GB storage |
| DO Spaces | — | $5/mo | Backups & artifacts, 250 GB included |
| OpenAI Vector Store | ~$0.15/mo | ~$0.15/mo | Per curated domain KB; priced by corpus size at $0.10/GB/day |
| `search_precedents` (PAIR) | Negligible | Negligible | Results cached in Redis; PAIR Search API (`search.pair.gov.sg`) is a free government API |
| **Subtotal Infrastructure** | **~$153/mo** | **~$248/mo** | Combined: ~$401/mo |

No Solace broker line item: the LangGraph pipeline runs in-process in the arq worker, so there is no broker pod and no broker PVC to provision or pay for. That's a saving over the prior SAM-based design of roughly $4/mo (block storage) + node headroom for the broker pod.

### Monthly Projections (Infrastructure + LLM)

| Volume | Cases/Month | Est. LLM Cost | Infrastructure | Total |
|---|---|---|---|---|
| Low | 50 | $105 – $130 | ~$401 | ~$530 |
| Medium | 200 | $420 – $520 | ~$401 | ~$920 |
| High | 500 | $1,050 – $1,300 | ~$401 | ~$1,700 |

> See [Part 8: Infrastructure Setup](08-infrastructure-setup.md) for detailed sizing and provisioning instructions.

---

## Appendix B: Environment Variables Reference

Mirrors `src/shared/config.py::Settings` and `.env.example`. The canonical list is in code — if this appendix disagrees with `Settings`, the code wins.

### Required Variables

| Variable | Description | Example |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API authentication key | `sk-proj-...` |
| `DATABASE_URL` | Postgres async DSN (SQLAlchemy + asyncpg). Production: `postgresql+asyncpg://vc_app:pass@private-db-xxx.db.ondigitalocean.com:25060/verdictcouncil?sslmode=require` | `postgresql://vc_dev:vc_dev_password@localhost:5432/verdictcouncil` |
| `REDIS_URL` | Redis DSN for arq + caching. Production: `rediss://default:pass@private-redis-xxx.db.ondigitalocean.com:25061/0` | `redis://localhost:6379/0` |
| `JWT_SECRET` | HS256 signing key for the `vc_token` auth cookie (rotate ≥ annually) | `(secret, 256-bit)` |
| `COOKIE_SECURE` | `true` in production (HTTPS); `false` for local HTTP | `false` |
| `FRONTEND_ORIGINS` | CORS allow-list (comma-separated) | `http://localhost:5173` |

### Application Configuration

| Variable | Description | Default |
|---|---|---|
| `NAMESPACE` | K8s namespace (also used as a metrics/logging tag) | `verdictcouncil` |
| `FASTAPI_HOST` | uvicorn bind address | `0.0.0.0` |
| `FASTAPI_PORT` | uvicorn bind port (Procfile.dev pins to 8001; only applies when starting uvicorn outside honcho) | `8000` |
| `LOG_LEVEL` | Python logging level | `INFO` |
| `PRECEDENT_CACHE_TTL_SECONDS` | Redis cache TTL for `search_precedents` results | `86400` (24h) |
| `PAIR_API_URL` | PAIR Search API endpoint | `https://search.pair.gov.sg/api/v1/search` |
| `PAIR_CIRCUIT_BREAKER_THRESHOLD` | Consecutive PAIR failures before opening the circuit | `3` |
| `PAIR_CIRCUIT_BREAKER_TIMEOUT` | Seconds before attempting to close a tripped circuit | `60` |
| `KB_MAX_UPLOAD_BYTES` | Judge KB per-file upload cap | `26214400` (25 MB) |
| `DOMAIN_UPLOADS_ENABLED` | Master switch for domain KB ingest | `true` |
| `CLASSIFIER_SANITIZER_ENABLED` | Run the `llm-guard` DeBERTa-v3 classifier during KB ingest | `true` |
| `RESET_TOKEN_TTL_MINUTES` | Password-reset token lifetime | `30` |
| `PASSWORD_RESET_BASE_URL` | Frontend URL for the password-reset link (token appended as `?token=`) | `http://localhost:5173/reset-password` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM_ADDRESS` | SMTP transport for password-reset emails (empty host logs the link at WARNING instead of sending) | — |

### OpenAI Configuration

| Variable | Description | Default |
|---|---|---|
| `OPENAI_VECTOR_STORE_ID` | Default vector store for the domain KB / judge KB | — |
| `OPENAI_MODEL_LIGHTWEIGHT` | Model for lightweight tier | `gpt-5.4-nano` |
| `OPENAI_MODEL_EFFICIENT_REASONING` | Model for efficient-reasoning tier | `gpt-5-mini` |
| `OPENAI_MODEL_STRONG_REASONING` | Model for strong-reasoning tier | `gpt-5` |
| `OPENAI_MODEL_FRONTIER_REASONING` | Model for frontier-reasoning tier | `gpt-5.4` |

### MLflow

| Variable | Description | Default |
|---|---|---|
| `MLFLOW_ENABLED` | Toggle MLflow tracing for agent + pipeline runs | `false` |
| `MLFLOW_TRACKING_URI` | Tracking server URL | `http://localhost:5001` |
| `MLFLOW_EXPERIMENT` | Experiment name under which pipeline/agent runs are recorded | `verdictcouncil-pipeline` |

### DigitalOcean-Specific Notes

| Topic | Detail |
|---|---|
| **PostgreSQL port** | DO Managed PostgreSQL uses port `25060` (not 5432). Connection strings from `doctl databases connection` include this. |
| **Redis TLS** | DO Managed Redis requires TLS. Use `rediss://` (double-s) scheme, not `redis://`. |
| **Private networking** | Use the private hostname (`private-db-...`) for connections from within the same VPC. The public hostname works but routes through the internet. |
| **SSL mode** | PostgreSQL connections should include `?sslmode=require` for encrypted connections. |

---

## Appendix C: Glossary

### Current architecture

| Term | Description |
|---|---|
| **LangGraph** | Python library for stateful graph-based agent workflows. The whole pipeline is one `StateGraph`. |
| **StateGraph** | LangGraph construct that holds nodes (agents) and edges (transitions). Defined in `src/pipeline/graph/builder.py`. |
| **Node** | A single function invocation inside the graph. In this codebase an "agent" is a node. |
| **CaseState** | The Pydantic domain model that nodes read and write. Lives in `src/shared/case_state.py`. |
| **GraphState** | The TypedDict the graph runtime sees — wraps `CaseState` plus `run_id`, `halt`, `retry_counts`, `extra_instructions`, etc. |
| **AsyncPostgresSaver** | LangGraph's Postgres checkpointer; persists `GraphState` to the `checkpoints` table after every node. |
| **`gate2_dispatch` / `gate2_join`** | Infrastructure nodes that bracket the parallel Gate-2 fan-out (evidence / fact / witness / legal-knowledge). |
| **`pre_run_guardrail`** | Graph node that runs an injection scan on submitted content before any agent executes. |
| **WhatIfController** | In-process service in `src/services/whatif_controller/controller.py` that deep-clones a completed `CaseState`, applies a modification, and re-enters the graph at the owning agent. |
| **`start_agent`** | `GraphState` field that lets a run bypass early nodes — used by gate-by-gate rerun and what-if scenarios. |
| **Checkpoint** | A persisted `GraphState` snapshot keyed by `thread_id` (= the pipeline `run_id`). Substrate for crash recovery, rerun, what-if. |
| **`audit_log`** | Append-only list on `CaseState` containing per-node `AuditEntry` records. Dedupe-merged by the `_merge_case` reducer. |
| **arq** | Redis-backed async task queue. Runs the pipeline graph off the request path; see `src/workers/worker_settings.py`. |
| **Outbox pattern** | Pipeline dispatch writes to Postgres (`pipeline_jobs`) before enqueuing to Redis; the worker claims with `FOR UPDATE SKIP LOCKED` to prevent double-dispatch on horizontal scale. |
| **llm-guard** | DeBERTa-v3 + regex prompt-injection classifier stack used on ingest and on every pipeline run via `pre_run_guardrail`. |
| **MLflow** | Observability backend recording per-agent and per-pipeline runs with prompts, responses, tool calls, token usage. |

### Domain vocabulary

| Abbreviation | Full Name | Description |
|---|---|---|
| **SCT** | Small Claims Tribunals | Singapore tribunal handling civil claims up to $20,000 (or $30,000 by consent) |
| **SCTA** | Small Claims Tribunals Act | Cap. 308, governing statute for SCT proceedings |
| **RTA** | Road Traffic Act | Cap. 276, governing statute for traffic offences in Singapore |
| **CPFTA** | Consumer Protection (Fair Trading) Act | Cap. 52A, consumer protection statute relevant to small claims |
| **SOGA** | Sale of Goods Act | Cap. 393, governing statute for sale of goods disputes |
| **PAIR** | Platform for AI-assisted Research | Singapore government legal research platform; indexes higher court decisions only (SGHC, SGCA, etc.) — does not cover SCT or lower State Courts |
| **eLitigation** | Electronic Litigation | Singapore judiciary's online platform for published judgments; source corpus indexed by PAIR |

### Infrastructure

| Abbreviation | Full Name | Description |
|---|---|---|
| **RAG** | Retrieval-Augmented Generation | Technique that retrieves relevant documents before generating LLM responses |
| **HPA** | Horizontal Pod Autoscaler | Kubernetes resource that automatically scales pod replicas based on metrics |
| **PVC** | Persistent Volume Claim | Kubernetes abstraction for requesting persistent storage |
| **DOCR** | DigitalOcean Container Registry | Private Docker image registry with native DOKS integration |
| **DOKS** | DigitalOcean Kubernetes Service | Managed Kubernetes with free control plane, automatic upgrades, integrated load balancing |
| **DO** | DigitalOcean | Cloud infrastructure provider hosting all VerdictCouncil services |
| **doctl** | DigitalOcean CLI | Command-line tool for managing DigitalOcean resources; used in CI/CD |
| **SemVer** | Semantic Versioning | Versioning scheme: MAJOR.MINOR.PATCH |
| **JWT** | JSON Web Token | Compact, URL-safe token format for authentication claims |
| **OCR** | Optical Character Recognition | Extracts text from images and scanned documents (used inside `parse_document`) |
| **TTL** | Time to Live | Duration for which cached data remains valid before expiry |
| **VPC** | Virtual Private Cloud | Isolated private network within DigitalOcean for secure inter-service communication |

### Historical (no longer in the architecture)

Left here only so that old PRs, commit messages, and ADRs remain readable.

| Term | Meaning (historical) |
|---|---|
| **SAM** | *Solace Agent Mesh* — the event-driven agent framework the project ran on before the LangGraph migration. Replaced entirely. |
| **A2A** | *Agent-to-Agent* protocol over Solace topics. No longer used; agents communicate via typed graph state. |
| **Solace PubSub+** | Enterprise message broker that previously carried inter-agent messages. Removed from the stack. |
| **Message VPN** | Solace virtual partition for isolating message traffic (not a network VPN). Obsolete. |
| **LiteLLM** | Multi-provider LLM abstraction layer bundled with SAM. Replaced by `langchain-openai.ChatOpenAI`. |
| **Layer2Aggregator** | Out-of-process fan-in barrier service. Replaced by the in-process `gate2_join` node. |
| **ADK / `google-adk`** | Google Agent Development Kit, used briefly for session storage. Replaced by `AsyncPostgresSaver` from `langgraph-checkpoint-postgres`. |

---
