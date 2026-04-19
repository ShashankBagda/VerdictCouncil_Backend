# Part 9: Local Development

This guide covers running the full VerdictCouncil stack on a developer's machine. Three modes are provided depending on what you're working on:

| Mode | What It Runs | Best For | Startup Time |
|---|---|---|---|
| **Hybrid** (recommended) | Infrastructure in Docker, agents natively | Daily agent development — fast iteration, live reload | ~30s |
| **Full Docker** | Everything in Docker Compose | Integration testing, onboarding new developers | ~2min |
| **Local K8s** | Everything in kind cluster | Testing K8s manifests before deploying to DOKS | ~5min |

---

## 9.1 Prerequisites

| Tool | Version | Install | Required For |
|---|---|---|---|
| Docker Desktop | 24+ | [docker.com](https://www.docker.com/products/docker-desktop/) | All modes |
| Python | 3.12 | `brew install python@3.12` | Hybrid mode |
| kind | 0.24+ | `brew install kind` | Local K8s mode only |
| kubectl | 1.31+ | `brew install kubectl` | Local K8s mode only |
| Make | any | Pre-installed on macOS | All modes (optional but recommended) |

### Docker Desktop Resources

The full stack runs 15 containers locally. Allocate sufficient resources in Docker Desktop settings:

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 4 cores | 6 cores |
| Memory | 6 GB | 8 GB |
| Disk | 20 GB | 40 GB |

---

## 9.2 Environment Configuration

### .env.example

Copy this to `.env` and fill in real values:

```bash
# .env.example — copy to .env and edit

# ──────────────────────────────────────────────
# Solace Event Broker (local Docker container)
# ──────────────────────────────────────────────
SOLACE_BROKER_URL=tcp://localhost:55555
SOLACE_BROKER_VPN=default
SOLACE_BROKER_USERNAME=admin
SOLACE_BROKER_PASSWORD=admin

# ──────────────────────────────────────────────
# PostgreSQL (local Docker container)
# ──────────────────────────────────────────────
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=verdictcouncil
POSTGRES_USER=vc_dev
POSTGRES_PASSWORD=vc_dev_password
DATABASE_URL=postgresql://vc_dev:vc_dev_password@localhost:5432/verdictcouncil

# ──────────────────────────────────────────────
# Redis (local Docker container)
# ──────────────────────────────────────────────
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_URL=redis://localhost:6379/0

# ──────────────────────────────────────────────
# OpenAI
# ──────────────────────────────────────────────
OPENAI_API_KEY=sk-proj-your-key-here
OPENAI_VECTOR_STORE_ID=vs_your-store-id
OPENAI_MODEL_LIGHTWEIGHT=gpt-5.4-nano
OPENAI_MODEL_EFFICIENT_REASONING=gpt-5-mini
OPENAI_MODEL_STRONG_REASONING=gpt-5
OPENAI_MODEL_FRONTIER_REASONING=gpt-5.4

# ──────────────────────────────────────────────
# Application
# ──────────────────────────────────────────────
NAMESPACE=verdictcouncil
FASTAPI_HOST=0.0.0.0
FASTAPI_PORT=8000
LOG_LEVEL=DEBUG
JWT_SECRET=dev-secret-do-not-use-in-production
PRECEDENT_CACHE_TTL_SECONDS=86400
PAIR_API_URL=https://search.pair.gov.sg/api/v1/search
```

**Important:** The `.env` file contains secrets. It is gitignored and must never be committed.

---

## 9.3 Mode 1: Hybrid (Recommended for Daily Development)

Run infrastructure (PostgreSQL, Redis, Solace) in Docker. Run agents natively with Python for fast iteration and debugger support.

### 9.3.1 docker-compose.infra.yml

This compose file starts only the infrastructure services:

```yaml
# docker-compose.infra.yml
# Infrastructure services for local development.
# Agents run natively — see "Running agents" below.

services:
  # ─── PostgreSQL ───────────────────────────────
  postgres:
    image: postgres:16
    container_name: vc-postgres
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB: verdictcouncil
      POSTGRES_USER: vc_dev
      POSTGRES_PASSWORD: vc_dev_password
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U vc_dev"]
      interval: 5s
      timeout: 5s
      retries: 5

  # ─── Redis ────────────────────────────────────
  redis:
    image: redis:7
    container_name: vc-redis
    ports:
      - "6379:6379"
    command: >
      redis-server
        --maxmemory 256mb
        --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  # ─── Solace PubSub+ Event Broker ─────────────
  solace:
    image: solace/solace-pubsub-standard:latest
    container_name: vc-solace
    ports:
      - "55555:55555"   # SMF (Solace Message Format)
      - "8080:8080"     # SEMP Management API
      - "1883:1883"     # MQTT
      - "5672:5672"     # AMQP
      - "9000:9000"     # REST
      - "8008:8008"     # Web Transport
    environment:
      username_admin_globalaccesslevel: admin
      username_admin_password: admin
      system_scaling_maxconnectioncount: "100"
    volumes:
      - solace_data:/var/lib/solace
    shm_size: 1g
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:5550/health-check/guaranteed-active || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 60s

volumes:
  postgres_data:
  redis_data:
  solace_data:
```

### 9.3.2 Start Infrastructure

```bash
# Start infrastructure services
docker compose -f docker-compose.infra.yml up -d

# Verify all services are healthy
docker compose -f docker-compose.infra.yml ps

# Expected output:
# vc-postgres   running (healthy)
# vc-redis      running (healthy)
# vc-solace     running (healthy)
```

### 9.3.3 Set Up Python Environment

```bash
# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install dependencies (from pyproject.toml)
pip install -e ".[dev]"

# Run database migrations
python -m alembic upgrade head
```

### 9.3.4 Run Agents Natively

Each agent is a separate process. Open a terminal per agent, or use a process manager:

```bash
# Load environment
source .venv/bin/activate
set -a && source .env && set +a

# Terminal 1: Web Gateway
python -m solace_agent_mesh.main --config configs/agents/web-gateway.yaml

# Terminal 2: Case Processing
python -m solace_agent_mesh.main --config configs/agents/case-processing.yaml

# Terminal 3: Complexity Routing
python -m solace_agent_mesh.main --config configs/agents/complexity-routing.yaml

# ... and so on for all 9 agents + layer2-aggregator + whatif-controller
```

### 9.3.5 Process Manager (Recommended)

Running 12 agents in separate terminals is unwieldy. Use a `Procfile` with a process manager like [honcho](https://github.com/nickstenning/honcho) or [overmind](https://github.com/DarthSim/overmind):

```procfile
# Procfile.dev
web-gateway:          python -m solace_agent_mesh.main --config configs/agents/web-gateway.yaml
case-processing:      python -m solace_agent_mesh.main --config configs/agents/case-processing.yaml
complexity-routing:   python -m solace_agent_mesh.main --config configs/agents/complexity-routing.yaml
evidence-analysis:    python -m solace_agent_mesh.main --config configs/agents/evidence-analysis.yaml
fact-reconstruction:  python -m solace_agent_mesh.main --config configs/agents/fact-reconstruction.yaml
witness-analysis:     python -m solace_agent_mesh.main --config configs/agents/witness-analysis.yaml
legal-knowledge:      python -m solace_agent_mesh.main --config configs/agents/legal-knowledge.yaml
argument-construction: python -m solace_agent_mesh.main --config configs/agents/argument-construction.yaml
deliberation:         python -m solace_agent_mesh.main --config configs/agents/deliberation.yaml
governance-verdict:   python -m solace_agent_mesh.main --config configs/agents/governance-verdict.yaml
layer2-aggregator:    python -m solace_agent_mesh.main --config configs/services/layer2-aggregator.yaml
whatif-controller:    python -m solace_agent_mesh.main --config configs/services/whatif-controller.yaml
```

```bash
# Using honcho (Python-based, installs via pip)
pip install honcho
honcho start -f Procfile.dev

# Using overmind (Go-based, supports selective restart)
brew install overmind
overmind start -f Procfile.dev

# Restart a single agent after code changes (overmind only)
overmind restart case-processing
```

### 9.3.6 Developing a Single Agent

When working on a specific agent, you only need that agent and its upstream dependencies running:

```bash
# Example: working on Legal Knowledge (Agent 6)
# Needs: infrastructure + layer2-aggregator (to receive merged CaseState)
# Does NOT need: Layer 4 agents (deliberation, governance-verdict)

# Start infra
docker compose -f docker-compose.infra.yml up -d

# Start only the agent you're developing
source .venv/bin/activate && set -a && source .env && set +a
python -m solace_agent_mesh.main --config configs/agents/legal-knowledge.yaml
```

To test in isolation, publish a mock CaseState directly to the agent's input topic using the Solace SEMP API or a test script:

```bash
# Solace management console (browser)
open http://localhost:8080   # Login: admin / admin
```

---

## 9.4 Mode 2: Full Docker Compose

Run everything — infrastructure and all agents — in Docker. Closest to production, but slower iteration (requires image rebuild on code changes).

### 9.4.1 docker-compose.yml

```yaml
# docker-compose.yml
# Full local stack: infrastructure + all agents.
# Use docker-compose.infra.yml instead for hybrid mode.

services:
  # ─── Infrastructure ───────────────────────────
  postgres:
    image: postgres:16
    container_name: vc-postgres
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB: verdictcouncil
      POSTGRES_USER: vc_dev
      POSTGRES_PASSWORD: vc_dev_password
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U vc_dev"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7
    container_name: vc-redis
    ports:
      - "6379:6379"
    command: >
      redis-server
        --maxmemory 256mb
        --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  solace:
    image: solace/solace-pubsub-standard:latest
    container_name: vc-solace
    ports:
      - "55555:55555"
      - "8080:8080"
      - "1883:1883"
      - "5672:5672"
      - "9000:9000"
      - "8008:8008"
    environment:
      username_admin_globalaccesslevel: admin
      username_admin_password: admin
      system_scaling_maxconnectioncount: "100"
    volumes:
      - solace_data:/var/lib/solace
    shm_size: 1g
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:5550/health-check/guaranteed-active || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 60s

  # ─── Database Migrations ──────────────────────
  migrations:
    build: .
    container_name: vc-migrations
    command: python -m alembic upgrade head
    env_file: .env
    environment:
      DATABASE_URL: postgresql://vc_dev:vc_dev_password@postgres:5432/verdictcouncil
    depends_on:
      postgres:
        condition: service_healthy
    restart: "no"

  # ─── Agents (single image, different configs) ──
  # All agents use the same Docker image. Each service overrides the
  # --config argument to point to its YAML config file.

  web-gateway:
    build: .
    container_name: vc-web-gateway
    command: ["--config", "/app/configs/gateway/web-gateway.yaml"]
    ports:
      - "8000:8000"
    env_file: .env
    environment:
      SOLACE_BROKER_URL: tcp://solace:55555
      DATABASE_URL: postgresql://vc_dev:vc_dev_password@postgres:5432/verdictcouncil
      REDIS_URL: redis://redis:6379/0
    depends_on:
      solace:
        condition: service_healthy
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      migrations:
        condition: service_completed_successfully

  case-processing:
    build: .
    container_name: vc-case-processing
    command: ["--config", "/app/configs/agents/case-processing.yaml"]
    env_file: .env
    environment: &agent-env
      SOLACE_BROKER_URL: tcp://solace:55555
      DATABASE_URL: postgresql://vc_dev:vc_dev_password@postgres:5432/verdictcouncil
      REDIS_URL: redis://redis:6379/0
    depends_on: &agent-deps
      solace:
        condition: service_healthy
      migrations:
        condition: service_completed_successfully

  complexity-routing:
    build: .
    container_name: vc-complexity-routing
    command: ["--config", "/app/configs/agents/complexity-routing.yaml"]
    env_file: .env
    environment: *agent-env
    depends_on: *agent-deps

  evidence-analysis:
    build: .
    container_name: vc-evidence-analysis
    command: ["--config", "/app/configs/agents/evidence-analysis.yaml"]
    env_file: .env
    environment: *agent-env
    depends_on: *agent-deps

  fact-reconstruction:
    build: .
    container_name: vc-fact-reconstruction
    command: ["--config", "/app/configs/agents/fact-reconstruction.yaml"]
    env_file: .env
    environment: *agent-env
    depends_on: *agent-deps

  witness-analysis:
    build: .
    container_name: vc-witness-analysis
    command: ["--config", "/app/configs/agents/witness-analysis.yaml"]
    env_file: .env
    environment: *agent-env
    depends_on: *agent-deps

  legal-knowledge:
    build: .
    container_name: vc-legal-knowledge
    command: ["--config", "/app/configs/agents/legal-knowledge.yaml"]
    env_file: .env
    environment: *agent-env
    depends_on: *agent-deps

  argument-construction:
    build: .
    container_name: vc-argument-construction
    command: ["--config", "/app/configs/agents/argument-construction.yaml"]
    env_file: .env
    environment: *agent-env
    depends_on: *agent-deps

  deliberation:
    build: .
    container_name: vc-deliberation
    command: ["--config", "/app/configs/agents/deliberation.yaml"]
    env_file: .env
    environment: *agent-env
    depends_on: *agent-deps

  governance-verdict:
    build: .
    container_name: vc-governance-verdict
    command: ["--config", "/app/configs/agents/governance-verdict.yaml"]
    env_file: .env
    environment: *agent-env
    depends_on: *agent-deps

  layer2-aggregator:
    build: .
    container_name: vc-layer2-aggregator
    command: ["--config", "/app/configs/services/layer2-aggregator.yaml"]
    env_file: .env
    environment:
      SOLACE_BROKER_URL: tcp://solace:55555
      REDIS_URL: redis://redis:6379/0
    depends_on:
      solace:
        condition: service_healthy
      redis:
        condition: service_healthy

  whatif-controller:
    build: .
    container_name: vc-whatif-controller
    command: ["--config", "/app/configs/services/whatif-controller.yaml"]
    env_file: .env
    environment: *agent-env
    depends_on:
      solace:
        condition: service_healthy
      migrations:
        condition: service_completed_successfully

volumes:
  postgres_data:
  redis_data:
  solace_data:
```

### 9.4.2 Usage

```bash
# Start the full stack
docker compose up -d --build

# View logs for a specific agent
docker compose logs -f case-processing

# Rebuild a single agent after code changes
docker compose up -d --build case-processing

# Stop everything
docker compose down

# Stop and remove all data (clean slate)
docker compose down -v
```

### 9.4.3 Volume Mounts for Live Reload

For faster iteration in full Docker mode, mount source code as volumes so changes are reflected without rebuilding:

```yaml
# docker-compose.override.yml (auto-loaded by docker compose)
# Mount source code for live reload during development.
# Do NOT commit this file.

services:
  case-processing:
    volumes:
      - ./src:/app/src:ro
      - ./configs:/app/configs:ro

  # Apply the same pattern to any agent you're actively developing:
  # evidence-analysis:
  #   volumes:
  #     - ./src:/app/src:ro
  #     - ./configs:/app/configs:ro
```

---

## 9.5 Mode 3: Local Kubernetes (kind)

Use [kind](https://kind.sigs.k8s.io/) (Kubernetes in Docker) to test K8s manifests locally before deploying to DOKS. This validates your deployments, services, configmaps, secrets, and ingress configuration.

### 9.5.1 Create kind Cluster

```yaml
# kind-config.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: vc-local
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 80
        hostPort: 80
        protocol: TCP
      - containerPort: 443
        hostPort: 443
        protocol: TCP
  - role: worker
  - role: worker
```

```bash
# Create cluster
kind create cluster --config kind-config.yaml

# Verify
kubectl cluster-info --context kind-vc-local
kubectl get nodes
```

### 9.5.2 Load Images into kind

kind runs its own container runtime, so Docker images must be loaded explicitly:

```bash
# Build all agent images locally
AGENTS=(
  web-gateway case-processing complexity-routing
  evidence-analysis fact-reconstruction witness-analysis
  legal-knowledge argument-construction deliberation
  governance-verdict layer2-aggregator whatif-controller
)

for agent in "${AGENTS[@]}"; do
  docker build -t verdictcouncil/${agent}:local -f docker/${agent}/Dockerfile .
  kind load docker-image verdictcouncil/${agent}:local --name vc-local
done
```

### 9.5.3 Deploy K8s Manifests

```bash
# Create namespace
kubectl create namespace verdictcouncil

# Create secrets (use local dev values)
kubectl create secret generic verdictcouncil-secrets \
  --namespace verdictcouncil \
  --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY}" \
  --from-literal=SOLACE_BROKER_USERNAME="admin" \
  --from-literal=SOLACE_BROKER_PASSWORD="admin" \
  --from-literal=POSTGRES_USER="vc_dev" \
  --from-literal=POSTGRES_PASSWORD="vc_dev_password" \
  --from-literal=JWT_SECRET="dev-secret-do-not-use-in-production"

# Apply local-specific configmap overrides
kubectl apply -f k8s/local/ --namespace verdictcouncil

# Or apply base manifests with local overrides via kustomize:
kubectl apply -k k8s/overlays/local/
```

### 9.5.4 Local K8s Manifest Structure

```
k8s/
├── base/                          # Shared manifests
│   ├── agent-configmap.yaml
│   ├── agent-deployment.yaml      # Template (one per agent)
│   ├── solace-broker-statefulset.yaml
│   ├── web-gateway-hpa.yaml
│   ├── ingress.yaml
│   └── secrets.yaml
├── overlays/
│   ├── local/                     # kind cluster overrides
│   │   ├── kustomization.yaml
│   │   ├── configmap-patch.yaml   # localhost hostnames, dev credentials
│   │   └── resource-patch.yaml    # Lower resource limits for laptop
│   ├── staging/                   # DOKS staging overrides
│   │   ├── kustomization.yaml
│   │   └── configmap-patch.yaml   # DO Managed DB hostnames
│   └── production/                # DOKS production overrides
│       ├── kustomization.yaml
│       └── configmap-patch.yaml
```

### 9.5.5 Kustomize Overlay for Local

```yaml
# k8s/overlays/local/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: verdictcouncil

resources:
  - ../../base

patches:
  - path: configmap-patch.yaml
  - path: resource-patch.yaml
```

```yaml
# k8s/overlays/local/configmap-patch.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: agent-common-config
data:
  SOLACE_BROKER_URL: "tcp://solace-broker-svc:55555"
  POSTGRES_HOST: "postgresql-svc"
  POSTGRES_PORT: "5432"
  POSTGRES_DB: "verdictcouncil"
  REDIS_URL: "redis://redis-svc:6379/0"
  LOG_LEVEL: "DEBUG"
```

```yaml
# k8s/overlays/local/resource-patch.yaml
# Reduce resource requests/limits for local development
apiVersion: apps/v1
kind: Deployment
metadata:
  name: case-processing
spec:
  template:
    spec:
      containers:
        - name: case-processing
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 256Mi
```

### 9.5.6 Cleanup

```bash
# Delete cluster
kind delete cluster --name vc-local
```

---

## 9.6 Makefile

A `Makefile` provides consistent commands across all development modes:

```makefile
# Makefile
.PHONY: help infra-up infra-down up down logs test lint migrate clean kind-up kind-down

SHELL := /bin/bash
COMPOSE_INFRA := docker compose -f docker-compose.infra.yml
COMPOSE_FULL  := docker compose

# ─── Help ───────────────────────────────────────
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ─── Infrastructure (Hybrid Mode) ──────────────
infra-up: ## Start infrastructure only (PostgreSQL, Redis, Solace)
	$(COMPOSE_INFRA) up -d
	@echo "Waiting for services to be healthy..."
	$(COMPOSE_INFRA) exec postgres pg_isready -U vc_dev
	@echo "Infrastructure ready."

infra-down: ## Stop infrastructure
	$(COMPOSE_INFRA) down

infra-logs: ## Tail infrastructure logs
	$(COMPOSE_INFRA) logs -f

# ─── Full Stack (Docker Mode) ──────────────────
up: ## Start full stack in Docker
	$(COMPOSE_FULL) up -d --build

down: ## Stop full stack
	$(COMPOSE_FULL) down

logs: ## Tail all logs
	$(COMPOSE_FULL) logs -f

logs-%: ## Tail logs for a specific service (e.g., make logs-case-processing)
	$(COMPOSE_FULL) logs -f $*

rebuild-%: ## Rebuild and restart a specific service (e.g., make rebuild-case-processing)
	$(COMPOSE_FULL) up -d --build $*

# ─── Python Development ────────────────────────
venv: ## Create virtual environment
	python3.12 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"

migrate: ## Run database migrations
	.venv/bin/python -m alembic upgrade head

agents: ## Start all agents via honcho (hybrid mode)
	honcho start -f Procfile.dev

agent-%: ## Start a single agent (e.g., make agent-case-processing)
	set -a && source .env && set +a && \
	.venv/bin/python -m solace_agent_mesh.main --config configs/agents/$*.yaml

# ─── Testing ───────────────────────────────────
test: ## Run all tests
	.venv/bin/pytest tests/ -v --cov=src --cov-report=term-missing

test-unit: ## Run unit tests only
	.venv/bin/pytest tests/unit/ -v

test-integration: infra-up ## Run integration tests (starts infra if needed)
	.venv/bin/pytest tests/integration/ -v --timeout=120

# ─── Code Quality ──────────────────────────────
lint: ## Run linter and type checker
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
	.venv/bin/mypy src/ --ignore-missing-imports

lint-fix: ## Auto-fix lint issues
	.venv/bin/ruff check --fix .
	.venv/bin/ruff format .

security: ## Run security scans
	.venv/bin/pip-audit --strict --desc
	.venv/bin/bandit -r src/ -ll -ii

# ─── Local Kubernetes (kind) ───────────────────
kind-up: ## Create kind cluster and load images
	kind create cluster --config kind-config.yaml
	@echo "Building and loading images..."
	@for agent in web-gateway case-processing complexity-routing \
		evidence-analysis fact-reconstruction witness-analysis \
		legal-knowledge argument-construction deliberation \
		governance-verdict layer2-aggregator whatif-controller; do \
		docker build -t verdictcouncil/$${agent}:local -f docker/$${agent}/Dockerfile . && \
		kind load docker-image verdictcouncil/$${agent}:local --name vc-local; \
	done
	kubectl apply -k k8s/overlays/local/

kind-down: ## Delete kind cluster
	kind delete cluster --name vc-local

# ─── Cleanup ───────────────────────────────────
clean: ## Stop everything and remove volumes
	$(COMPOSE_FULL) down -v 2>/dev/null || true
	$(COMPOSE_INFRA) down -v 2>/dev/null || true
	kind delete cluster --name vc-local 2>/dev/null || true
	rm -rf .venv
```

---

## 9.7 Solace Broker Local Setup

The Solace PubSub+ Standard Edition Docker image is free for development. After starting it, the broker needs topic configuration for the agent pipeline.

### 9.7.1 Management Console

```bash
# Open Solace management UI
open http://localhost:8080

# Default credentials: admin / admin
```

### 9.7.2 Automated Topic Provisioning

SAM agents auto-create topic subscriptions when they connect to the broker. No manual topic provisioning is required — starting an agent will register its subscription to the corresponding input topic.

However, for the **default Message VPN**, you may need to enable certain features:

```bash
# Enable the default VPN for development (via SEMP v2 API)
curl -s -X PATCH http://localhost:8080/SEMP/v2/config/msgVpns/default \
  -u admin:admin \
  -H "Content-Type: application/json" \
  -d '{
    "authenticationBasicEnabled": true,
    "enabled": true,
    "maxConnectionCount": 100,
    "maxMsgSpoolUsage": 1500
  }'

# Create a client username for agents
curl -s -X POST http://localhost:8080/SEMP/v2/config/msgVpns/default/clientUsernames \
  -u admin:admin \
  -H "Content-Type: application/json" \
  -d '{
    "clientUsername": "vc-agent",
    "password": "admin",
    "enabled": true
  }'
```

### 9.7.3 Verify Broker Connectivity

```bash
# Check broker health
curl -s http://localhost:5550/health-check/guaranteed-active

# List active client connections (after starting agents)
curl -s http://localhost:8080/SEMP/v2/monitor/msgVpns/default/clients \
  -u admin:admin | python -m json.tool
```

---

## 9.8 Differences Between Local and Production

| Aspect | Local (docker-compose / kind) | Production (DOKS) |
|---|---|---|
| **PostgreSQL** | Docker container, port 5432, no TLS | DO Managed, port 25060, TLS required (`sslmode=require`) |
| **Redis** | Docker container, port 6379, no auth | DO Managed, port 25061, TLS required (`rediss://`) |
| **Solace** | Docker container, default VPN | StatefulSet in DOKS, dedicated VPN |
| **Networking** | localhost / Docker network | VPC private networking |
| **TLS** | Not configured | Let's Encrypt via cert-manager |
| **Secrets** | `.env` file | Kubernetes Secrets |
| **Scaling** | Single replica per agent | HPA on web-gateway (2-5 replicas) |
| **Monitoring** | Docker logs / stdout | Prometheus + Grafana + DO Monitoring |
| **Database backups** | Manual (`pg_dump`) | Automated daily (DO Managed) |
| **Image registry** | Local Docker images | DOCR (`registry.digitalocean.com`) |

### Environment-Specific Connection Strings

| Environment | DATABASE_URL | REDIS_URL |
|---|---|---|
| Local | `postgresql://vc_dev:vc_dev_password@localhost:5432/verdictcouncil` | `redis://localhost:6379/0` |
| Docker Compose | `postgresql://vc_dev:vc_dev_password@postgres:5432/verdictcouncil` | `redis://redis:6379/0` |
| kind | `postgresql://vc_dev:vc_dev_password@postgresql-svc:5432/verdictcouncil` | `redis://redis-svc:6379/0` |
| DOKS (staging/prod) | `postgresql://vc_app:...@private-db-xxx.db.ondigitalocean.com:25060/verdictcouncil?sslmode=require` | `rediss://default:...@private-redis-xxx.db.ondigitalocean.com:25061/0` |

---

## 9.9 Troubleshooting

### Solace Broker Won't Start

```bash
# Solace requires shared memory — ensure shm_size is set
docker compose -f docker-compose.infra.yml logs solace

# If "not enough shared memory", increase shm_size in compose file or:
docker run --shm-size=1g solace/solace-pubsub-standard:latest

# Solace takes 30-60s to fully start. Wait for health check to pass:
docker compose -f docker-compose.infra.yml ps
```

### Port Conflicts

```bash
# Check what's using a port
lsof -i :5432   # PostgreSQL
lsof -i :6379   # Redis
lsof -i :55555  # Solace SMF
lsof -i :8080   # Solace SEMP (also used by many dev tools)
lsof -i :8000   # Web Gateway

# If 8080 is taken by another service, remap Solace SEMP:
# In docker-compose: "8081:8080" instead of "8080:8080"
```

### Agent Can't Connect to Solace

```bash
# Verify broker is accepting connections
curl -s http://localhost:5550/health-check/guaranteed-active

# Check that .env has correct SOLACE_BROKER_URL
# For hybrid mode (agents on host): tcp://localhost:55555
# For Docker mode (agents in containers): tcp://solace:55555
```

### Database Migration Fails

```bash
# Check PostgreSQL is running and accepting connections
docker compose -f docker-compose.infra.yml exec postgres pg_isready -U vc_dev

# Run migrations with verbose output
python -m alembic upgrade head --verbose

# If schema is corrupted, reset (destroys data):
docker compose -f docker-compose.infra.yml down -v
docker compose -f docker-compose.infra.yml up -d postgres
# Wait for healthy, then re-migrate
```

### Docker Desktop Memory Pressure

```bash
# Check container resource usage
docker stats --no-stream

# If running out of memory, stop non-essential agents:
docker compose stop witness-analysis whatif-controller

# Or reduce Solace memory in compose file:
# environment:
#   system_scaling_maxconnectioncount: "50"  # lower from 100
# shm_size: 512m  # lower from 1g
```

---

## 9.10 Quick Start Summary

```bash
# 1. Clone and enter the repo
git clone <repo-url> && cd VerdictCouncil_Backend
git checkout development

# 2. Set up environment
cp .env.example .env
# Edit .env — add your OPENAI_API_KEY

# 3. Start infrastructure
make infra-up

# 4. Set up Python
make venv
source .venv/bin/activate
make migrate

# 5. Start all agents
make agents

# 6. Open the web gateway
open http://localhost:8000/health
# → {"status": "ok"}

# 7. Open Solace management console
open http://localhost:8080
# → Login: admin / admin
```

---
