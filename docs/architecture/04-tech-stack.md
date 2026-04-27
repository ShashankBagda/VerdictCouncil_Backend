# Part 4: Tech Stack

## 4.1 Technology Matrix

| Category | Technology | Version | Justification |
|---|---|---|---|
| **Runtime** | Python | 3.12 | Modern type hints, improved asyncio performance, PEP 695 generics |
| **Agent Framework** | LangGraph | `>=0.2.0` | Stateful, graph-based orchestration with in-process node dispatch, typed shared state, built-in checkpointing, and first-class support for conditional routing and parallel fan-out |
| **LLM Integration** | langchain-openai + openai SDK | `>=0.2` / `~=2.32` | `ChatOpenAI` for agent nodes (tool binding, structured output); raw `openai` client for File API and direct vector-store calls |
| **Checkpointer** | langgraph-checkpoint-postgres | `>=2.0` | `AsyncPostgresSaver` persists graph state to Postgres after every node — enables crash recovery, audit replay, and what-if rewind |
| **LLM Provider** | OpenAI | API v1 | Tiered model strategy: `gpt-5.4` (frontier reasoning), `gpt-5` (strong reasoning), `gpt-5-mini` (efficient reasoning), `gpt-5.4-nano` (lightweight tasks) |
| **RAG Pipeline** | OpenAI Vector Stores | — | Managed parsing, chunking, embedding (text-embedding-3-large), and hybrid retrieval used for the domain knowledge base and judge-scoped KB |
| **Document Storage** | OpenAI Files API | — | Native PDF, image, and text parsing via the `parse_document` tool; no custom extraction libraries |
| **API Framework** | FastAPI + uvicorn | `>=0.115` / `>=0.34` | Async HTTP layer, OpenAPI schema generation, dependency injection for auth and DB sessions |
| **Background Workers** | arq | `~=0.26` | Redis-backed async task queue; runs the pipeline graph off the request path with outbox-claim semantics |
| **Database** | DigitalOcean Managed PostgreSQL | 16 | Automated daily backups, point-in-time recovery, read replicas, connection pooling; accessed via private VPC networking |
| **ORM** | SQLAlchemy (async) + Alembic | `>=2.0.35` / `>=1.14` | Async ORM via asyncpg; Alembic for schema migrations, run as a K8s one-shot Job (`alembic-migrate`) before each Deployment roll |
| **Cache / Queue** | DigitalOcean Managed Redis | 7 | arq job queue, precedent-search result cache, PAIR rate-limit token bucket |
| **Authentication** | PyJWT | `>=2.10` | HS256 JWT in `vc_token` httpOnly cookie; session hash persisted to Postgres to prevent replay |
| **HTTP Client** | httpx | `>=0.28` | Async client for the `search_precedents` tool (PAIR Search API at `search.pair.gov.sg`) and other outbound calls |
| **Observability** | LangSmith + OpenTelemetry | — | LangSmith for graph-level tracing, prompt versioning, and offline eval (`eval.yml`); OTLP exporter ships FastAPI/middleware spans to whatever collector the environment provides |
| **Input Defenses** | llm-guard | `0.3.16` | Regex + DeBERTa-v3 classifier stack guarding document ingestion against indirect prompt injection |
| **Containerization** | Docker | — | Single multi-stage image used for both the `api-service` Deployment (uvicorn) and the `arq-worker` Deployment (arq) — same image, different `command`/`args` |
| **Container Registry** | DigitalOcean Container Registry (DOCR) | — | Native DOKS integration (no image-pull secrets), same-region pull latency, integrated vulnerability scanning |
| **Compute Platform (backend)** | DigitalOcean Kubernetes (DOKS) | 1.31+ | Live backend deployment target. Two Deployments (`api-service`, `arq-worker`), one CronJob (`stuck-case-watchdog`), one one-shot Job (`alembic-migrate`), NGINX Ingress with cert-manager TLS. See `k8s/`. Chosen over App Platform because the rubric rewards Kubernetes deploys |
| **Compute Platform (frontend)** | DigitalOcean App Platform | — | Static-site deploy of the Vite build. Auto-builds on push, served from DO's global edge with managed TLS. See `../../VerdictCouncil_Frontend/.do/app.production.yaml` |
| **Load Balancer** | DigitalOcean Load Balancer | — | Auto-provisioned by the NGINX Ingress controller; HTTPS termination via cert-manager, HTTP→HTTPS redirect, health checks |
| **Object Storage** | DigitalOcean Spaces | — | S3-compatible storage for database backups and CI artifacts (currently unused at runtime — documents are stored as `bytea` in Postgres) |
| **CI/CD** | GitHub Actions + doctl | — | `digitalocean/action-doctl@v2` for DOCR + DOKS auth. `staging-deploy.yml` and `production-deploy.yml` build the image, push to DOCR, render the `verdictcouncil-secrets` Secret, run `alembic upgrade head` as a Job, then roll both Deployments |
| **Monitoring** | DO Monitoring + Prometheus + LangSmith | — | DO provides cluster-level metrics; Prometheus scrapes `/metrics` on `api-service`; LangSmith captures graph-level traces |
| **Logging** | Structured stdout (JSON) | — | DOKS collects stdout via log drivers; the graph runner emits per-node entries (node name, duration, token usage) that also flow to LangSmith |
| **Code Quality** | ruff + mypy | `>=0.8` / `>=1.14` | Fast linting (ruff) and static type checking (mypy) enforced in CI |
| **Security Scanning** | pip-audit + bandit | `>=2.7` / `>=1.8` | Dependency vulnerability scanning (pip-audit) and Python code security analysis (bandit) |
| **Testing** | pytest + pytest-asyncio + factory-boy | `>=8.3` / `>=0.25` / `>=3.3` | Unit and integration tests; the `integration` marker gates tests that require a live Postgres, Redis, or OpenAI |

## 4.2 Model Selection Strategy

Each agent is assigned a model tier based on reasoning-depth requirements. Tiers are wired in `src/shared/config.py` and mapped to agents in `src/pipeline/graph/prompts.py`.

| Tier | Model | Use Case | Agents |
|---|---|---|---|
| **Lightweight** | gpt-5.4-nano | Parsing, classification, low-complexity extraction | case-processing, complexity-routing |
| **Efficient Reasoning** | gpt-5-mini | Witness assessment with reasoning traces | witness-analysis |
| **Strong Reasoning** | gpt-5 | Detailed analysis requiring broad context | evidence-analysis, fact-reconstruction, legal-knowledge |
| **Frontier Reasoning** | gpt-5.4 | Complex legal reasoning, fairness auditing, final hearing analysis | argument-construction, hearing-analysis, hearing-governance |

Models are overridable per environment via `OPENAI_MODEL_LIGHTWEIGHT`, `OPENAI_MODEL_EFFICIENT_REASONING`, `OPENAI_MODEL_STRONG_REASONING`, and `OPENAI_MODEL_FRONTIER_REASONING`.

## 4.3 Key Design Decisions

| Decision | Choice | Alternatives Considered | Rationale |
|---|---|---|---|
| Agent framework | LangGraph | CrewAI, AutoGen, bespoke orchestration, Solace Agent Mesh (previous) | Typed shared state, native conditional routing and parallel fan-out, Postgres checkpointer, no broker dependency, full control of node code — simpler ops than the prior SAM/Solace stack with equivalent audit properties via MLflow + Postgres |
| LLM wiring | `langchain-openai.ChatOpenAI` per agent | Direct `openai` SDK in every node | Tool binding, structured-output enforcement, and retry/backoff come for free; a thin wrapper in `common._run_agent_node` standardises prompt assembly |
| Checkpointing | `AsyncPostgresSaver` (LangGraph) | In-memory, Redis | Durability and replay are requirements for judicial audit; Postgres is already in the stack |
| Task queue | arq (Redis) | Celery, Dramatiq | Pure-async, small surface area, first-class `async def` handlers that match the FastAPI codebase; outbox pattern prevents double-dispatch |
| RAG approach | OpenAI Vector Stores | Self-hosted (Chroma, Weaviate, Pinecone) | Zero infrastructure overhead; automatic chunking and embedding; managed hybrid retrieval eliminates tuning |
| Live precedent source | PAIR Search API only | PAIR + judiciary.gov.sg scraping | judiciary.gov.sg has no usable search API; PAIR indexes the full eLitigation higher court corpus (SGHC, SGCA, SGHCF, SGHCR, SGHC(I), SGHC(A), SGCA(I)) with hybrid BM25 + semantic retrieval. Does not cover SCT or lower State Courts — those decisions are generally unpublished. A curated vector store fills this gap with manually sourced domain-specific content. |
| Database hosting | DO Managed PostgreSQL | Self-hosted StatefulSet | Automated backups, failover, patching; no K8s StatefulSet to operate; private VPC access |
| Cache hosting | DO Managed Redis | Self-hosted StatefulSet | Managed HA, TLS by default, no operational overhead; private VPC access |
| Container registry | DOCR | GHCR, Docker Hub, ECR | Native DOKS integration eliminates image-pull secrets, same-region pull latency, integrated vulnerability scanning |
| Backend compute | DOKS (Kubernetes) | App Platform, droplets, AWS Fargate | Rubric rewards a Kubernetes deployment, and DOKS gives explicit control over Deployments, Services, Ingress, and CronJobs. App Platform was considered (simpler ops) but trades away too much for this assessment. Trade-off: more provisioning effort than App Platform; acceptable given the rubric |
| Frontend compute | DigitalOcean App Platform (static site) | GitHub Pages, Cloudflare Pages | The frontend is a Vite static build; App Platform auto-builds on push, serves from a global edge, manages TLS — exactly the shape of the artifact. No reason to put a static site through K8s |
| Cloud platform | DigitalOcean | AWS, GCP, Azure | Simpler pricing model, managed K8s + Postgres + Redis + App Platform in one console, sufficient for judicial workload scale, lower cost for small-to-medium deployments |
| Auth transport | HTTP-only cookies | Bearer tokens in headers | XSS protection; automatic inclusion in browser requests; no client-side token storage |

---
