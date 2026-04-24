# VerdictCouncil — Backend

FastAPI + 9-agent Solace Agent Mesh (SAM) backend for VerdictCouncil, a judicial AI decision-support system for Singapore lower courts (Small Claims Tribunal and Traffic Violations cases). Judges upload case materials, run multi-agent AI analysis through a 4-gate human-review pipeline, and record their own judicial decisions.

## Table of Contents

1. [Architecture at a Glance](#architecture-at-a-glance)
2. [The 9 Agents](#the-9-agents)
3. [Prerequisites](#prerequisites)
4. [Local Development](#local-development)
5. [Environment Variables](#environment-variables)
6. [API Surface](#api-surface)
7. [Database & Migrations](#database--migrations)
8. [Testing](#testing)
9. [Contract Checks](#contract-checks)
10. [Deployment](#deployment)
11. [Further Reading](#further-reading)
12. [Contributing](#contributing)

---

## Architecture at a Glance

Four cooperating processes form the runtime (see `Procfile.dev`):

| Process | What it does | Port |
|---------|-------------|------|
| **FastAPI app** (`src/api/app.py`) | HTTP/JSON API for the frontend; also hosts the what-if controller in-process | 8001 |
| **SAM web-gateway** (`configs/gateway/web-gateway.yaml`) | HTTP/SSE bridge into the Solace Agent Mesh A2A bus | 8002 |
| **9 SAM agents** (`configs/agents/*.yaml`) | Specialist AI agents running as separate SAM processes over the Solace broker | — |
| **layer2-aggregator** (`src/services/layer2_aggregator/`) | Custom SAM app that collects per-agent responses and writes final case state to Redis/PostgreSQL | — |

From the orchestration root, `./dev.sh` starts all four layers. For deeper architecture see [`docs/architecture/02-system-architecture.md`](docs/architecture/02-system-architecture.md) and the full [`docs/architecture/README.md`](docs/architecture/README.md).

---

## The 9 Agents

| Agent | Description |
|-------|-------------|
| **case-processing** | Intake router. Parses incoming submissions, normalises the case schema, classifies domain (SCT or traffic), and validates jurisdiction before the pipeline proceeds. |
| **complexity-routing** | Classifies case complexity (low/medium/high) and routes to automated processing, judicial review, or human escalation. First halt point for high-complexity cases. |
| **evidence-analysis** | Impartial evidence examiner. Classifies, scores, and cross-references all submitted evidence; surfaces contradictions, admissibility risks, and evidentiary gaps. |
| **fact-reconstruction** | Builds a sourced chronological timeline from evidence and witness material; marks disputed facts and their dependency on unresolved testimony. |
| **witness-analysis** | Assesses witness credibility, anticipates testimony for traffic cases, and flags material inconsistencies for the Judge to resolve at hearing. |
| **legal-knowledge** | Retrieves applicable statutes and precedents via the curated knowledge base and PAIR Search API; supplies verbatim statutory text and binding higher-court authority. |
| **argument-construction** | Constructs balanced prosecution/defence (traffic) or claimant/respondent (SCT) arguments for judicial evaluation, noting weaknesses on both sides. |
| **hearing-analysis** | Hearing preparation core. Produces a step-by-step reasoning chain from evidence to a preliminary conclusion at Gate 3, citing every source and flagging low-confidence steps for the presiding Judge. |
| **hearing-governance** | Final governance gate (Gate 4). Audits the full pipeline output for bias and logical gaps, then presents a governance summary for the presiding Judge to review before recording their own decision. Does not produce a verdict recommendation. |

Full YAML configurations in `configs/agents/`. See [`docs/architecture/03-agent-configurations.md`](docs/architecture/03-agent-configurations.md) for detailed configuration and tool lists.

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12 | `brew install python@3.12` |
| PostgreSQL | 15+ | `brew install postgresql@15` |
| Redis | 7+ | `brew install redis` |
| Docker | latest | [docker.com](https://docker.com) |
| `make` | — | pre-installed on macOS |

---

## Local Development

```bash
cp .env.example .env          # fill in your secrets (see Environment Variables below)
make install                  # create .venv, install dependencies
make infra-up                 # start Postgres, Redis, and Solace broker via Docker
make solace-bootstrap         # provision VPN + vc-agent user (first run only)
make migrate                  # run Alembic migrations
make dev                      # start all processes via honcho (Procfile.dev)
```

`make dev` starts the full local stack: web-gateway, 9 agents, layer2-aggregator, and the FastAPI API on port 8001. From the orchestration root, `./dev.sh` wraps all of the above.

To stop: `Ctrl+C` in the `dev.sh` terminal, or from the orchestration root:

```bash
./stop.sh          # stop backend + frontend; infra keeps running
./stop.sh --infra  # also tear down Docker infra
```

Full local dev guide: [`docs/architecture/09-local-development.md`](docs/architecture/09-local-development.md).

---

## Environment Variables

Copy `.env.example` and fill in the required values. The table below covers required variables; see `.env.example` for optional ones (PAIR circuit-breaker tuning, SMTP for password-reset email, KB upload cap).

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI API key — required for all agents |
| `SOLACE_BROKER_URL` | Broker SMF URL, e.g. `tcp://localhost:55556` |
| `SOLACE_BROKER_VPN` | VPN name, e.g. `verdictcouncil` |
| `SOLACE_BROKER_USERNAME` | Agent credentials user, e.g. `vc-agent` |
| `SOLACE_BROKER_PASSWORD` | Agent credentials password |
| `DATABASE_URL` | PostgreSQL DSN, e.g. `postgresql://vc_dev:pwd@localhost:5432/verdictcouncil` |
| `REDIS_URL` | Redis DSN, e.g. `redis://localhost:6379/0` |
| `JWT_SECRET` | Secret for signing `vc_token` cookies — use a long random string in production |
| `COOKIE_SECURE` | `true` in production (HTTPS); `false` for local HTTP |
| `FRONTEND_ORIGINS` | CORS allow-list, e.g. `http://localhost:5173` |
| `NAMESPACE` | SAM namespace, e.g. `verdictcouncil` |
| `FASTAPI_PORT` | API port (default `8001`) |
| `WEB_GATEWAY_PORT` | Gateway port (default `8002`) — **must differ from** `FASTAPI_PORT` |
| `OPENAI_VECTOR_STORE_ID` | ID of the curated judicial knowledge base vector store |

Optional model-tier overrides (`OPENAI_MODEL_LIGHTWEIGHT`, `OPENAI_MODEL_EFFICIENT_REASONING`, `OPENAI_MODEL_STRONG_REASONING`, `OPENAI_MODEL_FRONTIER_REASONING`) default to the values in `.env.example`.

---

## API Surface

All application routes live under `/api/v1/*`. Auth uses cookie-based JWT — the `vc_token` httpOnly cookie is set on login and required for all protected endpoints.

| Route group | Notes |
|-------------|-------|
| `auth` | Login, logout, session, password-reset |
| `cases` | CRUD, status, pipeline trigger, gate review (`/gates/{gate}/approve`) |
| `case_data` | Documents, hearing notes |
| `what_if` | Contestable Judgment Mode — scenario perturbation |
| `judge` | Judge-specific tools |
| `hearing_notes` / `hearing_pack` | Hearing preparation |
| `reopen_requests` | Request to reopen a closed case |
| `audit` | Immutable audit log |
| `dashboard` | Aggregated case stats |
| `health` | Readiness + liveness |
| `precedents` | PAIR Search API proxy |
| `knowledge-base` | Vector store upload/search (admin + senior_judge) |
| `admin` | System configuration |
| `/metrics` | Prometheus scrape endpoint |

Full contract: [`docs/openapi.md`](docs/openapi.md) and [`docs/openapi.json`](docs/openapi.json) (regenerated via `make openapi-snapshot`).

---

## Database & Migrations

PostgreSQL 16 via SQLAlchemy. Alembic at `alembic/` with versioned migrations in `alembic/versions/`. On a fresh database, use `make reset-db` (drops and recreates schema from models, then stamps Alembic to head) instead of `make migrate` to avoid a SQLAlchemy 2.x enum double-create bug in the initial migration.

```bash
make migrate          # alembic upgrade head
make infra-up         # start Postgres if not running
```

---

## Testing

```bash
make test             # pytest — unit + integration
make test-cov         # with coverage report
```

Test layout:

| Directory | Contents |
|-----------|----------|
| `tests/unit/` | Routes, services, tools, pipeline runner, guardrails, rate-limit, sanitization, diff engine, watchdog |
| `tests/integration/` | SAM mesh smoke, halt conditions, PG-backed watchdog |
| `tests/eval/` | Eval harness (`eval_runner.py`, fixture cases) |

---

## Contract Checks

The backend commits `docs/openapi.json` as the canonical API contract. The frontend lints its API client against this snapshot.

```bash
make openapi-snapshot   # regenerate docs/openapi.json from the FastAPI app
make openapi-check      # fail CI if docs/openapi.json is out of sync
make smoke-contract     # hit every frontend-used endpoint against a running API
```

---

## Deployment

| Artifact | Location |
|----------|----------|
| Container image | `Dockerfile` (multi-stage Python 3.12 slim; WeasyPrint libs included) |
| Local infra | `docker-compose.infra.yml` (Postgres 16, Redis 7, Solace PubSub+) |
| Kubernetes | `k8s/base/` — one Deployment per agent, plus gateway, aggregator, and API; HPA, ingress, Solace HA, bootstrap Job, stuck-case CronJob |
| Overlays | `k8s/overlays/staging/` and `k8s/overlays/production/` (kustomize) |

Infrastructure setup: [`docs/architecture/08-infrastructure-setup.md`](docs/architecture/08-infrastructure-setup.md).
Solace HA runbook: [`docs/operations/solace-ha-runbook.md`](docs/operations/solace-ha-runbook.md).

---

## Further Reading

- [`docs/architecture/README.md`](docs/architecture/README.md) — index of all 9 architecture docs (user stories, system architecture, agent configs, tech stack, diagrams, CI/CD, contestable judgment mode, infra setup, local dev)
- [`docs/operations/uat-runbook.md`](docs/operations/uat-runbook.md) — manual UAT procedure
- [`CLAUDE.md`](CLAUDE.md) — gitflow, branching rules, PR template, versioning, and commit conventions

---

## Contributing

Feature branches from `development`, PR back into `development`. Rules, PR template, and versioning conventions are in `CLAUDE.md`. This repo uses the [`linear-sdlc`](https://github.com/douglasswm/linear-sdlc) Claude Code skill suite for ticket-driven work — see `CLAUDE.md` for setup.
