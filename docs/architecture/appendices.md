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

### Infrastructure Costs (DigitalOcean)

| Item | Staging | Production | Notes |
|---|---|---|---|
| DOKS Nodes | $96/mo (2× s-4vcpu-8gb) | $144/mo (3× s-4vcpu-8gb) | Control plane is free; auto-scale adds $48/node |
| DOKS Load Balancer | $12/mo | $12/mo | Auto-provisioned by ingress controller |
| Managed PostgreSQL | $30/mo (s-1vcpu-2gb) | $60/mo (s-2vcpu-4gb) | Daily backups included; +$60 for HA standby |
| Managed Redis | $15/mo (s-1vcpu-1gb) | $15/mo (s-1vcpu-2gb) | TLS included; eviction policy: allkeys-lru |
| DOCR (Professional) | — | $12/mo | Shared registry, 50 GB storage |
| DO Spaces | — | $5/mo | Backups & artifacts, 250 GB included |
| Block Storage (Solace) | $4/mo (20 GB) | $4/mo (20 GB) | $0.10/GB/mo for Solace PVC |
| OpenAI Vector Store | ~$0.15/mo | ~$0.15/mo | 50 MB statute corpus at $0.10/GB/day |
| search_precedents (live) | Negligible | Negligible | Results cached in Redis; judiciary.gov.sg and PAIR are free public APIs |
| **Subtotal Infrastructure** | **~$157/mo** | **~$252/mo** | Combined: ~$409/mo |

### Monthly Projections (Infrastructure + LLM)

| Volume | Cases/Month | Est. LLM Cost | Infrastructure | Total |
|---|---|---|---|---|
| Low | 50 | $105 - $130 | ~$409 | ~$540 |
| Medium | 200 | $420 - $520 | ~$409 | ~$930 |
| High | 500 | $1,050 - $1,300 | ~$409 | ~$1,710 |

> See [Part 8: Infrastructure Setup](08-infrastructure-setup.md) for detailed sizing and provisioning instructions.

## Appendix B: Environment Variables Reference

### Required Variables

| Variable | Description | Example | Used By |
|---|---|---|---|
| `OPENAI_API_KEY` | OpenAI API authentication key | `sk-proj-...` | All agents |
| `SOLACE_BROKER_URL` | Solace broker connection URL | `tcp://solace-broker-svc:55555` | All agents, web-gateway |
| `SOLACE_BROKER_VPN` | Solace message VPN name | `verdictcouncil` | All agents, web-gateway |
| `SOLACE_BROKER_USERNAME` | Solace broker username | `vc-agent` | All agents, web-gateway |
| `SOLACE_BROKER_PASSWORD` | Solace broker password | `(secret)` | All agents, web-gateway |
| `DATABASE_URL` | DO Managed PostgreSQL connection string | `postgresql://vc_app:pass@private-db-xxx.db.ondigitalocean.com:25060/verdictcouncil?sslmode=require` | All agents, web-gateway |
| `REDIS_URL` | DO Managed Redis connection string | `rediss://default:pass@private-redis-xxx.db.ondigitalocean.com:25061/0` | legal-knowledge, web-gateway |
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

### DigitalOcean-Specific Notes

| Topic | Detail |
|---|---|
| **PostgreSQL port** | DO Managed PostgreSQL uses port `25060` (not 5432). Connection strings from `doctl databases connection` include this. |
| **Redis TLS** | DO Managed Redis requires TLS. Use `rediss://` (double-s) scheme, not `redis://`. |
| **Private networking** | Use the private hostname (`private-db-...`) for connections from within the same VPC. The public hostname works but routes through the internet. |
| **SSL mode** | PostgreSQL connections should include `?sslmode=require` for encrypted connections. |

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
| **DOCR** | DigitalOcean Container Registry | Private Docker image registry with native DOKS integration and vulnerability scanning |
| **DOKS** | DigitalOcean Kubernetes Service | Managed Kubernetes with free control plane, automatic upgrades, and integrated load balancing |
| **DO** | DigitalOcean | Cloud infrastructure provider hosting all VerdictCouncil services |
| **doctl** | DigitalOcean CLI | Command-line tool for managing DigitalOcean resources; used in CI/CD for deployment |
| **SemVer** | Semantic Versioning | Versioning scheme: MAJOR.MINOR.PATCH |
| **PAIR** | Platform for AI-assisted Research | Singapore government legal research platform |
| **JWT** | JSON Web Token | Compact, URL-safe token format for authentication claims |
| **OCR** | Optical Character Recognition | Technology to extract text from images and scanned documents |
| **TTL** | Time to Live | Duration for which cached data remains valid before expiry |
| **VPC** | Virtual Private Cloud | Isolated private network within DigitalOcean for secure inter-service communication |

---
