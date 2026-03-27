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
| **Database** | DigitalOcean Managed PostgreSQL | 16 | Automated daily backups, point-in-time recovery, read replicas, connection pooling; accessed via private VPC networking |
| **Cache** | DigitalOcean Managed Redis | 7 | Managed HA with automatic failover, TLS encryption, eviction policies; accessed via private VPC networking |
| **Authentication** | PyJWT | — | Lightweight JWT generation and validation; tokens transported via HTTP-only cookies |
| **HTTP Client** | httpx | — | Async HTTP client for search_precedents tool calls to judiciary.gov.sg and PAIR |
| **Containerization** | Docker | — | Multi-stage builds; per-agent images for independent scaling and deployment |
| **Container Registry** | DigitalOcean Container Registry (DOCR) | — | Native DOKS integration (no image pull secrets needed); private registry with vulnerability scanning |
| **Orchestration** | DigitalOcean Kubernetes Service (DOKS) | 1.31+ | Managed control plane, automatic upgrades, integrated load balancer, `do-block-storage` StorageClass for PVCs |
| **Load Balancer** | DigitalOcean Load Balancer | — | Auto-provisioned by DOKS ingress; HTTPS termination, HTTP→HTTPS redirect, health checks |
| **Object Storage** | DigitalOcean Spaces | — | S3-compatible storage for database backups, CI artifacts, and document archives |
| **CI/CD** | GitHub Actions + doctl | — | Build, test, deploy with `digitalocean/action-doctl@v2` for DOKS/DOCR authentication |
| **Monitoring** | DO Monitoring + Prometheus + Grafana | — | DO provides cluster-level metrics; Prometheus for agent-level metrics, Grafana for dashboards |
| **Logging** | Python stdout + Solace audit trail | — | DOKS collects stdout via log drivers; Solace provides message-level audit for every agent hop |
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
| Database hosting | DO Managed PostgreSQL | Self-hosted StatefulSet | Automated backups, failover, and patching; no K8s StatefulSet management; private VPC access |
| Cache hosting | DO Managed Redis | Self-hosted StatefulSet | Managed HA, TLS by default, no operational overhead; private VPC access |
| Container registry | DOCR | GHCR, Docker Hub, ECR | Native DOKS integration eliminates image pull secrets; same-region pull latency; integrated vulnerability scanning |
| Cloud platform | DigitalOcean | AWS, GCP, Azure | Simpler pricing model; managed K8s without enterprise complexity; sufficient for judicial workload scale; lower cost for small-to-medium deployments |
| Auth transport | HTTP-only cookies | Bearer tokens in headers | XSS protection; automatic inclusion in browser requests; no client-side token storage |

---
