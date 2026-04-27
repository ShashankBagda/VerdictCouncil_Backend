# VerdictCouncil — Backend

FastAPI backend for VerdictCouncil, a judicial AI decision-support system for Singapore lower courts (Small Claims Tribunal and Traffic Violations cases). Judges upload case materials, run multi-agent AI analysis through a 4-gate human-review pipeline (powered by an in-process LangGraph `StateGraph`), and record their own judicial decisions.

## Table of Contents

1. [Architecture at a Glance](#architecture-at-a-glance)
2. [The Reasoning Graph](#the-reasoning-graph)
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

Two long-lived processes form the runtime (see `Procfile.dev`), both built from the same multi-stage Docker image:

| Process | What it does | Port |
|---------|-------------|------|
| **FastAPI app** (`src/api/app.py`) | HTTP/JSON API for the frontend; hosts the LangGraph runtime for synchronous calls (resume after gate, what-if branching) | 8001 |
| **arq worker** (`src/workers/worker_settings.py`) | Drains the `pipeline_jobs` Postgres outbox; runs the LangGraph `StateGraph` end-to-end for asynchronous case processing; hosts cron jobs (domain reconciliation) | — |

There is **no message broker, no per-agent container, and no Orchestrator service.** Earlier drafts described a Solace Agent Mesh (SAM) topology with 9 per-agent containers and a `layer2-aggregator`; that design was decommissioned. Agents run in-process inside a LangGraph `StateGraph` (`src/pipeline/graph/builder.py`).

From the orchestration root, `./dev.sh` starts both processes plus the frontend. For deeper architecture see [`docs/architecture/02-system-architecture.md`](docs/architecture/02-system-architecture.md) and the full [`docs/architecture/README.md`](docs/architecture/README.md).

---

## The Reasoning Graph

The compiled LangGraph topology has three reasoning phases plus four research subagents (parallel fan-out) and four HITL gates:

```
intake → gate1 → research_dispatch ─Send─▶ research_evidence  ┐
                                          research_facts      │
                                          research_witnesses  │
                                          research_law        ┘
                                          → research_join → gate2
                                                              → synthesis → gate3 → auditor → gate4 → END
```

| Phase node | Description |
|---|---|
| **intake** | Parses the submission, normalises the case schema, classifies domain (SCT or traffic), validates jurisdiction, and assesses complexity. |
| **research_evidence** | Impartial evidence examiner. Classifies, scores, and cross-references submitted evidence; surfaces contradictions, admissibility risks, and evidentiary gaps. |
| **research_facts** | Builds a sourced chronological timeline; marks disputed facts and their dependency on unresolved testimony. |
| **research_witnesses** | Assesses witness credibility, anticipates testimony for traffic cases, and flags material inconsistencies. |
| **research_law** | Retrieves applicable statutes and precedents via the curated knowledge base and PAIR Search API; supplies verbatim statutory text and binding higher-court authority. |
| **synthesis** | Constructs balanced prosecution/defence (traffic) or claimant/respondent (SCT) arguments and a step-by-step reasoning chain to a preliminary conclusion at Gate 3, citing every source. |
| **auditor** | Final governance gate (Gate 4). Audits the full pipeline output for bias and logical gaps, then presents a governance summary for the presiding Judge to review before recording their own decision. |

Each gate (`gate1` … `gate4`) is a pair of LangGraph nodes: a `*_pause` that calls `interrupt(...)` and a `*_apply` that returns `Command(goto=...)` based on the reviewer's `advance` / `rerun` / `halt` decision. State persists in the Postgres `langgraph_checkpoint` table (`thread_id` = case `run_id`), so a paused run resumes cleanly from the API after the human review. See [`docs/architecture/03-agent-configurations.md`](docs/architecture/03-agent-configurations.md) for prompts, tools, and model tiers.

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
make infra-up                 # start Postgres + Redis via Docker
make migrate                  # run Alembic migrations
make dev                      # start the API + arq worker via honcho (Procfile.dev)
```

`make dev` starts the FastAPI API on port 8001 and the arq worker that drains the pipeline outbox. From the orchestration root, `./dev.sh` wraps all of the above plus the frontend.

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
| `DATABASE_URL` | PostgreSQL DSN, e.g. `postgresql://vc_dev:pwd@localhost:5432/verdictcouncil` |
| `REDIS_URL` | Redis DSN, e.g. `redis://localhost:6379/0` |
| `JWT_SECRET` | Secret for signing `vc_token` cookies — use a long random string in production |
| `COOKIE_SECURE` | `true` in production (HTTPS); `false` for local HTTP |
| `FRONTEND_ORIGINS` | CORS allow-list, e.g. `http://localhost:5173` |
| `NAMESPACE` | Logical environment namespace, e.g. `verdictcouncil` / `verdictcouncil-staging` |
| `FASTAPI_PORT` | API port (default `8001`) |
| `OPENAI_VECTOR_STORE_ID` | ID of the curated judicial knowledge base vector store |
| `LANGSMITH_API_KEY` | Optional. Enables LangSmith tracing + eval; runtime works without it |

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
| `tests/integration/` | LangGraph end-to-end smoke, gate halt/rerun conditions, PG-backed watchdog |
| `tests/eval/` | LangSmith eval harness (`eval_runner.py`, fixture cases) |

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
| Local infra | `docker-compose.infra.yml` (Postgres 16, Redis 7) |
| Kubernetes | `k8s/base/` — `api-service` Deployment, `arq-worker` Deployment, ClusterIP service, NGINX Ingress + cert-manager TLS, Alembic Job, stuck-case-watchdog CronJob |
| Overlays | `k8s/overlays/staging/` and `k8s/overlays/production/` (kustomize) |
| CI/CD | `.github/workflows/staging-deploy.yml` and `production-deploy.yml` — build the image, push to DOCR, render secrets, run Alembic, roll both Deployments |

Backend deploys to **DOKS** (DigitalOcean Kubernetes); the frontend deploys to **DO App Platform** as a static site (see `../VerdictCouncil_Frontend/.do/`). Infrastructure setup: [`docs/architecture/08-infrastructure-setup.md`](docs/architecture/08-infrastructure-setup.md). K8s layout: [`k8s/README.md`](k8s/README.md).

---

## Pending operator setup

The Sprint 4 backend cutover landed several features that require human
configuration before they are live:

- **Eval gate (`.github/workflows/eval.yml`)** — needs repo secrets
  (`LANGSMITH_API_KEY`, `OPENAI_API_KEY`), repo variable
  (`EVAL_BASELINE_EXPERIMENT`), and branch-protection rules. The
  workflow file is committed but inert until the secrets/variable land.
- **Worker-side `Command(resume=...)` cutover** — the contract layer
  for the `interrupt()`-driven HITL flow is shipped, but the worker
  (`run_gate_job`) still routes through the legacy `runner.run_gate(...)`
  path. `publish_interrupt()` is defined but unwired in production.
- **Frontend Sentry → LangSmith tagging** — needs a `VITE_SENTRY_DSN`
  in the frontend `.env` once Sprint 4 4.C5.1 lands.

Full checklist: [`docs/operations/sprint4-manual-ops.md`](docs/operations/sprint4-manual-ops.md).
Eval-gate specifics: [`docs/setup-2026-04-25.md`](docs/setup-2026-04-25.md).

---

## Further Reading

- [`docs/architecture/README.md`](docs/architecture/README.md) — index of all 9 architecture docs (user stories, system architecture, agent configs, tech stack, diagrams, CI/CD, contestable judgment mode, infra setup, local dev)
- [`docs/operations/uat-runbook.md`](docs/operations/uat-runbook.md) — manual UAT procedure
- [`docs/operations/sprint4-manual-ops.md`](docs/operations/sprint4-manual-ops.md) — Sprint 4 pending manual operations
- [`CLAUDE.md`](CLAUDE.md) — gitflow, branching rules, PR template, versioning, and commit conventions

---

## Contributing

Feature branches from `development`, PR back into `development`. Rules, PR template, and versioning conventions are in `CLAUDE.md`. This repo uses the [`linear-sdlc`](https://github.com/douglasswm/linear-sdlc) Claude Code skill suite for ticket-driven work — see `CLAUDE.md` for setup.
