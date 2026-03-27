# Part 4: Tech Stack

## 4.1 Technology Matrix

| Category | Technology | Version | Justification |
|---|---|---|---|
| **Runtime** | Python | 3.12 | SAM is Python-native; 3.12 offers improved performance and better type hints |
| **Agent Framework** | Solace Agent Mesh (SAM) | latest | Event-driven multi-agent orchestration with YAML-based agent configuration, built-in broker integration |
| **LLM Provider** | OpenAI | API v1 | Multi-model strategy: gpt-5.4 (frontier reasoning), gpt-5 (strong reasoning), gpt-5-mini (efficient reasoning), gpt-5.4-nano (lightweight tasks) |
| **LLM Abstraction** | LiteLLM (via SAM) | — | SAM uses LiteLLM internally for unified model routing and fallback handling |
| **Agent Protocol** | A2A via Solace topics | — | Asynchronous, decoupled inter-agent communication over publish/subscribe topics |
| **Message Broker** | Solace PubSub+ Event Broker | latest | Enterprise-grade event mesh with guaranteed delivery, topic hierarchy, message replay, and built-in audit trail |
| **RAG Pipeline** | OpenAI Vector Stores | — | Fully managed RAG: automatic parsing, chunking, embedding (text-embedding-3-large), and hybrid retrieval (semantic + keyword) |
| **Document Storage** | OpenAI Files API | — | Native PDF, image, and text parsing with no custom extraction libraries required |
| **Database** | PostgreSQL | 16 | Case records, audit logs, session persistence; JSONB support for flexible nested data |
| **Cache** | Redis | 7 | search_precedents caching (24h TTL), session token storage, rate limiting counters |
| **Authentication** | PyJWT | — | Lightweight JWT generation and validation; tokens transported via HTTP-only cookies |
| **HTTP Client** | httpx | — | Async HTTP client for search_precedents tool calls to judiciary.gov.sg and PAIR |
| **Containerization** | Docker | — | Multi-stage builds; per-agent images for independent scaling and deployment |
| **Container Registry** | GitHub Container Registry (GHCR) | — | Integrated with GitHub Actions CI/CD; organisation-scoped image access |
| **Orchestration** | Kubernetes | 1.28+ | 11+ pods: 9 agents + 1 gateway + 1 broker + infrastructure (PostgreSQL, Redis) |
| **CI/CD** | GitHub Actions | — | Build, test, deploy with environment promotion aligned to gitflow branching |
| **Monitoring** | Prometheus + Grafana | — | Agent health metrics, pipeline latency histograms, LLM token usage and cost tracking |
| **Logging** | Python stdout + Solace audit trail | — | Kubernetes collects stdout via log drivers; Solace provides message-level audit for every agent hop |
| **Code Quality** | ruff + mypy | — | Fast linting (ruff) and static type checking (mypy) enforced in CI |
| **Security Scanning** | pip-audit + bandit | — | Dependency vulnerability scanning (pip-audit) and Python code security analysis (bandit) |
| **Testing** | pytest | — | Unit and integration tests with mocked OpenAI calls; coverage target 80%+ |

## 4.2 Model Selection Strategy

Each agent is assigned a model based on reasoning depth requirements:

| Tier | Model | Use Case | Agents |
|---|---|---|---|
| **Lightweight** | gpt-5.4-nano | Parsing, classification, low-complexity extraction | CaseProcessing |
| **Fast Extraction** | gpt-5.4-nano | Quick analytical decisions, complexity routing | ComplexityRouting |
| **Efficient Reasoning** | gpt-5-mini | Witness assessment with reasoning traces | WitnessAnalysis |
| **Strong Reasoning** | gpt-5 | Detailed analysis requiring broad context | EvidenceAnalysis, FactReconstruction, LegalKnowledge |
| **Frontier Reasoning** | gpt-5.4 | Complex legal reasoning, fairness auditing, final verdicts | ArgumentConstruction, Deliberation, GovernanceVerdict |

## 4.3 Key Design Decisions

| Decision | Choice | Alternatives Considered | Rationale |
|---|---|---|---|
| RAG approach | OpenAI Vector Stores | Self-hosted (Chroma, Weaviate, Pinecone) | Zero infrastructure overhead; automatic chunking and embedding; managed hybrid retrieval eliminates tuning |
| Broker | Solace PubSub+ | RabbitMQ, Kafka, NATS | SAM-native integration; topic hierarchy maps to agent pipeline; enterprise audit trail for judicial compliance |
| Agent framework | SAM | LangGraph, CrewAI, AutoGen | Purpose-built for Solace broker; YAML-driven agent config; built-in tool registration and message routing |
| Database | PostgreSQL | MongoDB, DynamoDB | Relational integrity for case-party-evidence relationships; JSONB for flexible nested fields; mature ecosystem |
| Auth transport | HTTP-only cookies | Bearer tokens in headers | XSS protection; automatic inclusion in browser requests; no client-side token storage |

---

