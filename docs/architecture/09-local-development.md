# Part 9: Local Development

This guide covers running the full VerdictCouncil backend on a developer's machine. After the responsible-AI refactor, local dev is one flow rather than three: dockerised infrastructure + natively run API and worker processes. No Solace broker, no per-agent containers.

For frontend + backend together, the orchestration root exposes `./dev.sh` which does all of this plus the Vite dev server — see the [root README](../../../README.md).

---

## 9.1 Prerequisites

| Tool | Version | Install |
|---|---|---|
| Docker Desktop | 24+ | [docker.com](https://www.docker.com/products/docker-desktop/) |
| Python | 3.12 | `brew install python@3.12` |
| Make | any | Pre-installed on macOS |
| Node 22 + npm | for the frontend, not needed for backend-only dev | `brew install node@22` |

### Docker Desktop Resources

The local infra stack is three containers (Postgres, Redis, MLflow). Allocate:

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 2 cores | 4 cores |
| Memory | 4 GB | 6 GB |
| Disk | 10 GB | 20 GB |

Memory headroom matters mainly because `llm-guard` loads a ~415 MB DeBERTa-v3 classifier the first time you ingest a domain document.

---

## 9.2 Environment Configuration

### `.env`

Copy `.env.example` to `.env` and fill in real values:

```bash
# Required
OPENAI_API_KEY=sk-proj-your-key-here
DATABASE_URL=postgresql://vc_dev:vc_dev_password@localhost:5432/verdictcouncil
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=dev-secret-do-not-use-in-production
COOKIE_SECURE=false   # true only when serving HTTPS

FRONTEND_ORIGINS=http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173

# Application
NAMESPACE=verdictcouncil
FASTAPI_HOST=0.0.0.0
FASTAPI_PORT=8001
LOG_LEVEL=DEBUG
PRECEDENT_CACHE_TTL_SECONDS=86400

# PAIR precedent API + circuit breaker
PAIR_API_URL=https://search.pair.gov.sg/api/v1/search
PAIR_CIRCUIT_BREAKER_THRESHOLD=3
PAIR_CIRCUIT_BREAKER_TIMEOUT=60

# OpenAI models (overrides for src/shared/config.py defaults)
OPENAI_VECTOR_STORE_ID=vs_...
OPENAI_MODEL_LIGHTWEIGHT=gpt-5.4-nano
OPENAI_MODEL_EFFICIENT_REASONING=gpt-5-mini
OPENAI_MODEL_STRONG_REASONING=gpt-5
OPENAI_MODEL_FRONTIER_REASONING=gpt-5.4

# RAG corpus sanitisation (indirect prompt injection defence)
DOMAIN_UPLOADS_ENABLED=true
CLASSIFIER_SANITIZER_ENABLED=true
```

`.env` is gitignored. The canonical list of settings lives in `src/shared/config.py::Settings`; `.env.example` mirrors it.

---

## 9.3 First-time setup

```bash
# 1. Install Python deps (also prefetches the llm-guard classifier)
make install

# 2. Start infrastructure (Postgres + Redis + MLflow)
make infra-up

# 3. Run migrations
make migrate

# 4. (Optional) Seed demo users and sample cases
.venv/bin/python -m scripts.seed_data
```

`make install` is idempotent; re-run after `pyproject.toml` changes.

---

## 9.4 Running the app

### `make dev` — API + worker together via honcho

`Procfile.dev` defines the two runtime processes:

```
api:         .venv/bin/uvicorn src.api.app:app --host 0.0.0.0 --port 8001 --reload
arq-worker:  .venv/bin/arq src.workers.worker_settings.WorkerSettings
```

Run both under a single supervisor:

```bash
make dev
# honcho -f Procfile.dev start
# Ctrl-C once stops both cleanly
```

The API serves on `http://localhost:8001`; the worker claims jobs off Redis as cases are submitted. MLflow UI is at `http://localhost:5001`.

### Run components individually

Useful when you want a debugger on one side:

```bash
# API only (with autoreload)
.venv/bin/uvicorn src.api.app:app --host 0.0.0.0 --port 8001 --reload

# Worker only (foreground; verbose)
.venv/bin/arq src.workers.worker_settings.WorkerSettings --watch src
```

The worker's `--watch src` flag is optional but mirrors the API's autoreload.

### From the orchestration root

If you also want the frontend, start everything with one command from `VER/`:

```bash
cd /path/to/VER
./dev.sh
```

`dev.sh` starts infra, runs migrations, seeds demo data, starts backend (`make dev`), and starts the Vite dev server on `http://localhost:5173`. Exit with Ctrl-C.

---

## 9.5 `docker-compose.infra.yml`

The only compose file this repo ships is the infra one. It runs Postgres, Redis, and MLflow — nothing else.

```yaml
# docker-compose.infra.yml (live)
services:
  postgres:
    image: postgres:16-alpine
    container_name: vc-postgres
    ports: ["5432:5432"]
    environment:
      POSTGRES_DB: verdictcouncil
      POSTGRES_USER: vc_dev
      POSTGRES_PASSWORD: vc_dev_password
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U vc_dev -d verdictcouncil"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: vc-redis
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  mlflow:
    image: ghcr.io/mlflow/mlflow:v2.18.0
    container_name: vc-mlflow
    ports: ["5001:5000"]
    volumes:
      - mlflowdata:/mlflow
    command: >-
      mlflow server --host 0.0.0.0 --port 5000
      --backend-store-uri sqlite:////mlflow/mlflow.db
      --artifacts-destination /mlflow/artifacts

volumes:
  pgdata: {}
  mlflowdata: {}
```

Start / stop via `make infra-up` / `make infra-down`. Add `-v` when bringing things down to discard the named volumes (`docker compose -f docker-compose.infra.yml down -v`).

### Why no full compose for the app?

The FastAPI API and the arq worker share an image with the production pods and run natively against the dockerised infra in dev. Running them in containers locally adds friction (cache misses, slow reload) without buying anything — LangGraph, arq, and OpenAI calls behave identically either way. If you specifically need to test the Docker image, `docker build -t vc:dev . && docker run --env-file .env -p 8001:8001 vc:dev`.

---

## 9.6 Makefile (live)

```makefile
install:         # python3.12 venv + editable install + prefetch llm-guard classifier
lint:            # ruff check + ruff format --check on src/ tests/
format:          # ruff --fix + ruff format
typecheck:       # mypy src/
test:            # pytest tests/ -v --tb=short
test-cov:        # pytest with coverage
migrate:         # alembic upgrade head
reset-db:        # wipe + recreate schema + stamp alembic to head
infra-up:        # docker compose -f docker-compose.infra.yml up -d
infra-down:      # docker compose -f docker-compose.infra.yml down
dev:             # honcho -f Procfile.dev start (api + arq-worker)
clean:           # remove venv + build artifacts
openapi-snapshot:# regenerate docs/openapi.json from the live FastAPI app
openapi-check:   # fail if docs/openapi.json is out of sync
smoke-contract:  # hit every frontend-used endpoint against a running API
```

Run `make help` for the live list — the Makefile is the source of truth.

---

## 9.7 Running a single LangGraph node in isolation

You don't run the 9 agents as separate processes anymore. Each agent is a node function; call it directly from a REPL or a test:

```python
# .venv/bin/python
from src.pipeline.graph.builder import build_graph
from src.pipeline.graph.checkpointer import get_checkpointer
from src.shared.case_state import CaseState

checkpointer = await get_checkpointer()
graph = build_graph(checkpointer)

# Run one agent against a minimal state (bypasses earlier nodes via start_agent)
state = CaseState(...)  # from a fixture or a loaded checkpoint
result = await graph.ainvoke({
    "case": state,
    "run_id": state.run_id,
    "start_agent": "evidence-analysis",
    "extra_instructions": {},
    "retry_counts": {},
    "halt": None,
    "mlflow_run_ids": {},
    "is_resume": False,
})
```

For focused agent iteration, point the API at a hand-constructed case and hit `POST /api/v1/cases/{id}/rerun-gate` (see `src/api/routes/cases.py`) — it lets you re-enter the graph at a named node without resubmitting the case.

---

## 9.8 Testing

```bash
make test                               # unit + fake-LLM agent tests; no infra required
make test-cov                           # with coverage report
INTEGRATION_TESTS=1 pytest tests/integration/ -v
pytest -m integration tests/            # same thing
```

Integration tests hit a real Postgres (use `make infra-up` first) and call the real OpenAI API (needs `OPENAI_API_KEY`). They are gated behind the `integration` pytest marker so they do not run in the default CI path. The `make test` target excludes them.

For API contract coverage (used by CI's DAST job): `pytest tests/integration/test_api_contract.py`.

---

## 9.9 Differences Between Local and Production

| Concern | Local | Production (DOKS) |
|---|---|---|
| Postgres | Dockerised 16-alpine, `postgres:vc_dev_password` | DO Managed Postgres 16, credentials from GitHub Secrets |
| Redis | Dockerised 7-alpine, no auth | DO Managed Redis 7, TLS + auth |
| MLflow | Local sqlite backend, artifacts on disk | ClusterIP `mlflow-svc`; Postgres-backed; artifacts on DO Spaces (target) |
| API + worker | honcho-managed processes on localhost | Two Deployments in `verdictcouncil` namespace |
| Cookies | `COOKIE_SECURE=false` (HTTP on localhost) | `COOKIE_SECURE=true`, served behind HTTPS |
| `DOMAIN_UPLOADS_ENABLED` | true | true (toggle to false during an incident) |
| `CLASSIFIER_SANITIZER_ENABLED` | true | true |

---

## 9.10 Troubleshooting

### Port conflict on 5432 / 6379 / 8001 / 5001

```bash
lsof -i :5432 -i :6379 -i :8001 -i :5001
# kill whatever's squatting
make infra-down && make infra-up
```

If the host's Postgres is always running, stop it (`brew services stop postgresql`) or re-map the container port: edit `docker-compose.infra.yml` to `"5433:5432"` and `DATABASE_URL` to match.

### `alembic upgrade head` fails

```bash
# Check the current revision
.venv/bin/python -m alembic current

# Completely wipe and re-init schema (DESTRUCTIVE)
make reset-db
```

`reset-db` drops every model table, recreates from SQLAlchemy metadata, and stamps Alembic to `head`. Demo data is lost; run `python -m scripts.seed_data` afterwards.

### `llm-guard` / DeBERTa-v3 prefetch fails

`make install` runs `scripts.prefetch_sanitizer_model` which downloads ~415 MB to the HuggingFace cache. If the first run failed (no network, rate limit), re-run it or set `CLASSIFIER_SANITIZER_ENABLED=false` temporarily — only the regex fast-path layer runs when the classifier is disabled.

### Worker not picking up jobs

Check: (a) the worker process is actually running, (b) `REDIS_URL` resolves, (c) the job was enqueued — inspect `pipeline_jobs` in Postgres. The outbox pattern means the job row lands in Postgres first; if it's there but not being claimed, it's a worker issue, not an API issue.

### Docker Desktop memory pressure

```bash
docker system prune -f              # drop dangling images
docker volume prune -f              # drop unused volumes (NOT pgdata if you want to keep seeds)
docker stats                        # verify live usage
```

Consider lowering the Docker Desktop memory allocation once infra is running — Postgres + Redis + MLflow together use < 2 GB at rest.

---

## 9.11 Quick Start Summary

```bash
cp .env.example .env
$EDITOR .env                        # set OPENAI_API_KEY etc.

make install                        # first time only
make infra-up
make migrate
python -m scripts.seed_data         # optional: demo data
make dev                            # api on :8001 + arq worker

# stop: Ctrl-C in the `make dev` window, then
make infra-down
```

For the full-stack experience (backend + frontend) from the orchestration root: `./dev.sh`.

---
