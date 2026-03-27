# Appendices

## Appendix A: Cost Model

### Per-Case Cost Breakdown

| # | Agent | Model | Est. Input Tokens | Est. Output Tokens | Est. Cost (USD) |
|---|---|---|---|---|---|
| 1 | CaseProcessing | gpt-5.4-nano | ~5,000 | ~2,000 | $0.004 |
| 2 | ComplexityRouting | gpt-5.4-nano | ~2,000 | ~500 | $0.001 |
| 3 | EvidenceAnalysis | gpt-5 | ~15,000 | ~5,000 | $0.069 |
| 4 | FactReconstruction | gpt-5 | ~10,000 | ~3,000 | $0.043 |
| 5 | WitnessAnalysis | gpt-5-mini | ~8,000 | ~3,000 | $0.008 |
| 6 | LegalKnowledge | gpt-5 | ~10,000 | ~5,000 | $0.063 |
| 7 | ArgumentConstruction | gpt-5.4 | ~12,000 | ~5,000 | $0.105 |
| 8 | Deliberation | gpt-5.4 | ~15,000 | ~5,000 | $0.113 |
| 9 | GovernanceVerdict | gpt-5.4 | ~10,000 | ~3,000 | $0.070 |
| | **Total LLM per case** | | **~87,000** | **~31,500** | **$0.40 - $0.55** |

### Infrastructure Costs

| Item | Cost | Notes |
|---|---|---|
| OpenAI Vector Store | ~$0.005/day | 50MB statute corpus at $0.10/GB/day |
| search_precedents (live) | Negligible | Results cached in Redis for 24h; judiciary.gov.sg and PAIR are free public APIs |
| Solace PubSub+ | $0/month | Community edition for development; enterprise licensing for production |
| Kubernetes cluster | Variable | Cloud-provider dependent; estimated 3-5 nodes for production |

### Monthly Projections

| Volume | Cases/Month | Est. LLM Cost | Vector Store | Total |
|---|---|---|---|---|
| Low | 50 | $105 - $130 | $0.15 | ~$130 |
| Medium | 200 | $420 - $520 | $0.15 | ~$520 |
| High | 500 | $1,050 - $1,300 | $0.15 | ~$1,300 |

## Appendix B: Environment Variables Reference

### Required Variables

| Variable | Description | Example | Used By |
|---|---|---|---|
| `OPENAI_API_KEY` | OpenAI API authentication key | `sk-proj-...` | All agents |
| `SOLACE_BROKER_URL` | Solace broker connection URL | `tcp://solace-broker-svc:55555` | All agents, web-gateway |
| `SOLACE_BROKER_VPN` | Solace message VPN name | `verdictcouncil` | All agents, web-gateway |
| `SOLACE_BROKER_USERNAME` | Solace broker username | `vc-agent` | All agents, web-gateway |
| `SOLACE_BROKER_PASSWORD` | Solace broker password | `(secret)` | All agents, web-gateway |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://user:pass@postgresql-svc:5432/verdictcouncil` | All agents, web-gateway |
| `REDIS_URL` | Redis connection string | `redis://redis-svc:6379/0` | legal-knowledge, web-gateway |
| `JWT_SECRET` | Secret key for JWT signing | `(secret, 256-bit)` | web-gateway |

### Application Configuration

| Variable | Description | Default | Used By |
|---|---|---|---|
| `NAMESPACE` | Kubernetes namespace | `verdictcouncil` | All pods |
| `FASTAPI_HOST` | Web gateway bind address | `0.0.0.0` | web-gateway |
| `FASTAPI_PORT` | Web gateway bind port | `8000` | web-gateway |
| `LOG_LEVEL` | Python logging level | `INFO` | All agents |
| `AGENT_NAME` | Agent identifier for logging/audit | `(per-agent)` | Each agent pod |
| `PRECEDENT_CACHE_TTL_SECONDS` | Redis cache TTL for precedent searches | `86400` (24h) | legal-knowledge |
| `JUDICIARY_BASE_URL` | Singapore judiciary website base URL | `https://www.judiciary.gov.sg` | legal-knowledge |
| `PAIR_BASE_URL` | PAIR search API base URL | `https://search.pair.gov.sg` | legal-knowledge |

### OpenAI Configuration

| Variable | Description | Default | Used By |
|---|---|---|---|
| `OPENAI_VECTOR_STORE_ID` | Vector store ID for statute corpus | `vs_...` | legal-knowledge |
| `OPENAI_MODEL_LIGHTWEIGHT` | Model for lightweight tasks | `gpt-5.4-nano` | case-processing, complexity-routing |
| `OPENAI_MODEL_EFFICIENT_REASONING` | Model for efficient reasoning | `gpt-5-mini` | witness-analysis |
| `OPENAI_MODEL_STRONG_REASONING` | Model for strong reasoning | `gpt-5` | evidence-analysis, fact-reconstruction, legal-knowledge |
| `OPENAI_MODEL_FRONTIER_REASONING` | Model for frontier reasoning | `gpt-5.4` | argument-construction, deliberation, governance-verdict |

### Database Configuration (PostgreSQL pod)

| Variable | Description | Example |
|---|---|---|
| `POSTGRES_DB` | Database name | `verdictcouncil` |
| `POSTGRES_USER` | Database superuser | `vc_admin` |
| `POSTGRES_PASSWORD` | Database password | `(secret)` |
| `PGDATA` | Data directory path | `/var/lib/postgresql/data/pgdata` |

## Appendix C: Glossary

| Abbreviation | Full Name | Description |
|---|---|---|
| **SAM** | Solace Agent Mesh | Event-driven multi-agent orchestration framework built on Solace PubSub+ |
| **A2A** | Agent-to-Agent | Communication protocol where agents exchange messages via broker topics |
| **SCT** | Small Claims Tribunals | Singapore tribunal handling civil claims up to $20,000 (or $30,000 by consent) |
| **SCTA** | Small Claims Tribunals Act | Cap. 308, governing statute for SCT proceedings |
| **RTA** | Road Traffic Act | Cap. 276, governing statute for traffic offences in Singapore |
| **CPFTA** | Consumer Protection (Fair Trading) Act | Cap. 52A, consumer protection statute relevant to small claims |
| **SOGA** | Sale of Goods Act | Cap. 393, governing statute for sale of goods disputes |
| **RAG** | Retrieval-Augmented Generation | Technique that retrieves relevant documents before generating LLM responses |
| **LiteLLM** | LiteLLM | Unified API abstraction layer for multiple LLM providers; used internally by SAM |
| **PubSub+** | Solace PubSub+ Event Broker | Enterprise message broker supporting publish/subscribe, queueing, and event mesh patterns |
| **VPN** | Message VPN | Solace virtual partition for isolating message traffic (not a network VPN) |
| **HPA** | Horizontal Pod Autoscaler | Kubernetes resource that automatically scales pod replicas based on metrics |
| **PVC** | Persistent Volume Claim | Kubernetes abstraction for requesting persistent storage |
| **GHCR** | GitHub Container Registry | Container image registry integrated with GitHub |
| **SemVer** | Semantic Versioning | Versioning scheme: MAJOR.MINOR.PATCH |
| **PAIR** | Platform for AI-assisted Research | Singapore government legal research platform |
| **JWT** | JSON Web Token | Compact, URL-safe token format for authentication claims |
| **OCR** | Optical Character Recognition | Technology to extract text from images and scanned documents |
| **TTL** | Time to Live | Duration for which cached data remains valid before expiry |

---

