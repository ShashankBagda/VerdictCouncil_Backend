# Part 6: CI/CD Pipeline

> **Reality vs. Target State — read this first**
>
> This document mirrors the live CI/CD, flags gaps, and points at the manifests you can actually `kubectl apply`. The target columns are aspirations tracked for follow-up — the YAML and behaviour described below are what runs today unless explicitly marked *(target)*.
>
> | Area | Live | Target |
> |---|---|---|
> | Staging trigger | Push to `development` | Push to `release/**` |
> | Production trigger | Push to `main` | Push to `main` (unchanged) + `v*` tag |
> | Image strategy | Single polyvalent image; role selected by `command`/`args` per Deployment | Same image; optional per-component slim layers later |
> | Cluster topology | `api-service` Deployment + `arq-worker` Deployment + `stuck-case-watchdog` CronJob | unchanged; HPA on `api-service` is a follow-up |
> | Agent topology | All agents in-process inside the LangGraph `StateGraph` (no per-agent Deployment) | Unchanged. Earlier drafts proposed 9 agent microservices; that design was decommissioned with the SAM/Solace removal |
> | Smoke / canary tests | Not implemented | Post-deploy smoke (staging) + canary (prod) |
> | Coverage gate | `--cov-fail-under=65` enforced | 80 |
> | SAST / SCA / DAST | Advisory (`continue-on-error: true`) | SAST hard fail; DAST gated on a live FastAPI + Postgres |
> | Release tagging / GitHub Release | Not automated | `gh release create` on successful prod deploy |

---

## 6.1 Platform Overview

VerdictCouncil deploys to **DigitalOcean**:

| Service | Purpose | Why |
|---|---|---|
| **DOKS** (DigitalOcean Kubernetes Service) | Container orchestration | Managed control plane, automatic upgrades, integrated load balancer |
| **DOCR** (DigitalOcean Container Registry) | Docker image storage | Native DOKS integration, no image pull secrets needed |
| **DO Managed PostgreSQL 16** | Case records, graph checkpoints, audit logs | Automated backups, failover, connection pooling |
| **DO Managed Redis 7** | arq queue, precedent cache, PAIR rate-limit tokens | Managed HA, TLS, eviction policies |
| **DO Load Balancer** | HTTPS ingress | Auto-provisioned by NGINX ingress controller; Let's Encrypt via cert-manager |
| **DO Spaces** | Backup storage, CI artifacts | S3-compatible object storage |

### CI/CD Platform

**GitHub Actions** drives all automation, using `doctl` for deployment.

| Workflow | Trigger | Purpose | Target |
|---|---|---|---|
| `ci.yml` | Push to any branch; PR into `development` or `main` | lint → unit tests (65% cov) → SAST (bandit + semgrep) → SCA (pip-audit + safety + cyclonedx-bom SBOM) → DAST (smoke FastAPI behind a Postgres service, header + contract checks) → docker build verification → security summary | — |
| `staging-deploy.yml` | Push to `development` *(live)* / `release/**` *(target)* | Build single image, push to DOCR as `rc-{sha}` and `staging-latest`, `kubectl apply -k k8s/overlays/staging/`, render secrets, run Alembic, roll `api-service` + `arq-worker` | DOKS `verdictcouncil-staging` |
| `production-deploy.yml` | Push to `main` | Build image with `v{semver}` + `latest` tags, `kubectl apply -k k8s/overlays/production/`, render secrets, run Alembic, roll `api-service` + `arq-worker` | DOKS `verdictcouncil` |

### GitHub Secrets Required

| Secret | Purpose |
|---|---|
| `DIGITALOCEAN_ACCESS_TOKEN` | All deploy workflows |
| `DOCR_REGISTRY` | Fully qualified registry prefix (e.g. `registry.digitalocean.com/verdictcouncil`) |
| `DOKS_STAGING_CLUSTER_ID` | Staging DOKS cluster ID |
| `DOKS_PRODUCTION_CLUSTER_ID` | Production DOKS cluster ID |
| `OPENAI_API_KEY` | Application secret — rendered into the K8s secret at deploy time |
| `OPENAI_VECTOR_STORE_ID` | Application secret — rendered at deploy time |
| `STAGING_DATABASE_URL`, `STAGING_REDIS_URL`, `STAGING_JWT_SECRET`, `STAGING_FRONTEND_ORIGINS` | Staging env wiring |
| `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, `FRONTEND_ORIGINS` | Production env wiring |

Application secrets are **not** stored as plain K8s secrets in git; the deploy job renders them from GitHub Secrets into `verdictcouncil-secrets` via `kubectl create secret --dry-run=client | kubectl apply -f -`. The pod reads them via `envFrom: secretRef`.

---

## 6.2 CI Workflow (live)

Seven jobs run on every push and on PRs into `development` or `main`. The DAST job spins up a Postgres service and a bare FastAPI instance; it runs basic header checks and the API contract tests. Security scans currently run in advisory mode — fix the findings on follow-up rather than trust the green tick.

```yaml
# .github/workflows/ci.yml — live (summary)
name: CI
on:
  push:
    branches: ["**"]
  pull_request:
    branches: [development, main]

jobs:
  lint:                 # ruff check + ruff format --check on src/ and tests/
  unit-tests:           # pytest --cov=src --cov-fail-under=65 (OPENAI_API_KEY blanked)
  sast:                 # bandit -r src/ + semgrep (p/security-audit, p/owasp-top-ten) → SARIF upload
  sca:                  # pip-audit --desc + safety check + cyclonedx-bom SBOM
  dast:                 # Postgres service; start uvicorn on :8000; header check; tests/integration/test_api_contract.py
  build:                # docker buildx with GHA cache (no push)
  security-summary:     # aggregates pip-audit + bandit output (advisory)
```

### Gaps vs. target

| Area | Today | Target |
|---|---|---|
| Type checking | Not run | `mypy src/` in `lint` job |
| Coverage gate | 65 | 80 |
| SAST enforcement | `continue-on-error: true` on bandit + semgrep | Hard failure on medium+ findings |
| Integration tests | Not run in CI (run locally via `INTEGRATION_TESTS=1`) | Dedicated `integration-tests` job with Postgres + Redis services |
| Frontend snapshot diffing | N/A here | See frontend repo |

---

## 6.3 Docker Strategy

### Single image, two runtimes

The API (uvicorn) and the arq worker ship from the same image. The K8s manifests override `command`/`args` to select the entrypoint.

```dockerfile
# Dockerfile — single source of truth for both api and worker
FROM python:3.12-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim AS runtime
WORKDIR /app
# WeasyPrint deps for PDF export (hearing-pack endpoint)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libcairo2 \
    libgdk-pixbuf-2.0-0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /install /usr/local
COPY src/ /app/src/
COPY configs/ /app/configs/
RUN groupadd -r vcagent && useradd -r -g vcagent vcagent
USER vcagent
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8001/api/v1/health')" || exit 1
ENTRYPOINT ["uvicorn"]
CMD ["src.api.app:app", "--host", "0.0.0.0", "--port", "8001"]
```

K8s overrides per Deployment (one Deployment per role; both run from the same image):

```yaml
# api-service Deployment (k8s/base/deployment-api-service.yaml)
command: ["uvicorn"]
args: ["src.api.app:app", "--host", "0.0.0.0", "--port", "8001"]

# arq-worker Deployment (k8s/base/deployment-arq-worker.yaml)
command: ["arq"]
args: ["src.workers.worker_settings.WorkerSettings"]
```

The arq worker hosts the LangGraph `StateGraph` in-process and drains the `pipeline_jobs` Postgres outbox; the agents are nodes in that graph, not separate Deployments. Earlier drafts of this section described a nine-Deployment per-agent split (one container per agent, fronted by its own ClusterIP Service, called over HTTP from a separate Orchestrator). That design was decommissioned with the SAM/Solace removal — there is no `src.agents.main` module, no `/invoke` HTTP contract, no `AGENT_HMAC_SECRET`, no `DISPATCH_MODE`. If we ever need per-agent containers again, this is the natural extension point, but it is not on the roadmap.

### Image naming

```
{DOCR_REGISTRY}/verdictcouncil:{tag}
```

| Stage | Tag | Source |
|---|---|---|
| Feature CI | (no push) | GHA cache only |
| Staging | `rc-{sha}` | `staging-deploy.yml` |
| Production | `v{semver}` + `latest` | `production-deploy.yml` (reads `git describe --tags --abbrev=0`) |

### DOCR Integration with DOKS

```bash
doctl kubernetes cluster registry add <cluster-id>
```

Once bound, DOKS nodes pull from DOCR without image pull secrets.

---

## 6.4 Staging Deploy Workflow (live)

```yaml
# .github/workflows/staging-deploy.yml — live
name: Deploy to Staging
on:
  push:
    branches: [development]

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: staging
    steps:
      - uses: actions/checkout@v4
      - uses: digitalocean/action-doctl@v2
        with: { token: ${{ secrets.DIGITALOCEAN_ACCESS_TOKEN }} }
      - run: doctl registry login

      - name: Build and push image
        run: |
          IMAGE=${{ secrets.DOCR_REGISTRY }}/verdictcouncil:rc-${{ github.sha }}
          docker build -t "$IMAGE" .
          docker push "$IMAGE"
          echo "IMAGE=$IMAGE" >> "$GITHUB_ENV"

      - name: Configure kubectl
        run: doctl kubernetes cluster kubeconfig save ${{ secrets.DOKS_STAGING_CLUSTER_ID }}

      - name: Apply manifests
        run: kubectl apply -k k8s/overlays/staging/

      - name: Render secrets
        run: |
          kubectl create secret generic verdictcouncil-secrets \
            --namespace verdictcouncil-staging \
            --from-literal=OPENAI_API_KEY=${{ secrets.OPENAI_API_KEY }} \
            --from-literal=DATABASE_URL=${{ secrets.STAGING_DATABASE_URL }} \
            --from-literal=REDIS_URL=${{ secrets.STAGING_REDIS_URL }} \
            --from-literal=JWT_SECRET=${{ secrets.STAGING_JWT_SECRET }} \
            --from-literal=OPENAI_VECTOR_STORE_ID=${{ secrets.OPENAI_VECTOR_STORE_ID }} \
            --from-literal=FRONTEND_ORIGINS=${{ secrets.STAGING_FRONTEND_ORIGINS }} \
            --from-literal=COOKIE_SECURE=true \
            --from-literal=NAMESPACE=verdictcouncil \
            --from-literal=FASTAPI_HOST=0.0.0.0 \
            --from-literal=FASTAPI_PORT=8001 \
            --dry-run=client -o yaml | kubectl apply -f -

      - name: Run database migrations
        run: |
          kubectl delete job alembic-migrate --namespace verdictcouncil-staging --ignore-not-found
          sed "s|verdictcouncil:latest|$IMAGE|g" k8s/base/job-alembic-migrate.yaml | \
            kubectl apply --namespace verdictcouncil-staging -f -
          kubectl wait --for=condition=complete job/alembic-migrate \
            --namespace verdictcouncil-staging --timeout=300s

      - name: Roll image
        run: |
          kubectl set image -n verdictcouncil-staging \
            deployment/api-service api-service=$IMAGE
          kubectl rollout status -n verdictcouncil-staging deployment --timeout=300s
```

**Known follow-ups** (not in the live workflow yet):

- Add a **smoke job** that hits `/api/v1/health`, logs in as the staging test user, submits a fixture case, and polls `/api/v1/cases/{id}` until `ready_for_review` or `escalated` (or fails after 300s).
- Once the arq-worker Deployment lands, roll both: `kubectl set image -n <ns> deployment/arq-worker arq-worker=$IMAGE`.

---

## 6.5 Production Deploy Workflow (live)

```yaml
# .github/workflows/production-deploy.yml — live
name: Deploy to Production
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: actions/checkout@v4
      - uses: digitalocean/action-doctl@v2
        with: { token: ${{ secrets.DIGITALOCEAN_ACCESS_TOKEN }} }
      - run: doctl registry login

      - name: Build and push image
        run: |
          TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "latest")
          IMAGE=${{ secrets.DOCR_REGISTRY }}/verdictcouncil:$TAG
          docker build -t "$IMAGE" .
          docker tag "$IMAGE" ${{ secrets.DOCR_REGISTRY }}/verdictcouncil:latest
          docker push "$IMAGE"
          docker push ${{ secrets.DOCR_REGISTRY }}/verdictcouncil:latest
          echo "IMAGE=$IMAGE" >> "$GITHUB_ENV"

      - name: Configure kubectl
        run: doctl kubernetes cluster kubeconfig save ${{ secrets.DOKS_PRODUCTION_CLUSTER_ID }}

      - name: Apply manifests
        run: kubectl apply -k k8s/overlays/production/

      - name: Render secrets
        run: |
          kubectl create secret generic verdictcouncil-secrets \
            --namespace verdictcouncil \
            --from-literal=OPENAI_API_KEY=${{ secrets.OPENAI_API_KEY }} \
            --from-literal=DATABASE_URL=${{ secrets.DATABASE_URL }} \
            --from-literal=REDIS_URL=${{ secrets.REDIS_URL }} \
            --from-literal=JWT_SECRET=${{ secrets.JWT_SECRET }} \
            --from-literal=OPENAI_VECTOR_STORE_ID=${{ secrets.OPENAI_VECTOR_STORE_ID }} \
            --from-literal=FRONTEND_ORIGINS=${{ secrets.FRONTEND_ORIGINS }} \
            --from-literal=COOKIE_SECURE=true \
            --from-literal=NAMESPACE=verdictcouncil \
            --from-literal=FASTAPI_HOST=0.0.0.0 \
            --from-literal=FASTAPI_PORT=8001 \
            --dry-run=client -o yaml | kubectl apply -f -

      - name: Run database migrations
        run: |
          kubectl delete job alembic-migrate --namespace verdictcouncil --ignore-not-found
          sed "s|verdictcouncil:latest|$IMAGE|g" k8s/base/job-alembic-migrate.yaml | \
            kubectl apply --namespace verdictcouncil -f -
          kubectl wait --for=condition=complete job/alembic-migrate \
            --namespace verdictcouncil --timeout=300s

      - name: Roll image
        run: |
          kubectl set image -n verdictcouncil deployment --all "*=$IMAGE"
          kubectl rollout status -n verdictcouncil deployment --timeout=300s
```

**Known follow-ups:**

- Add a **canary job** post-deploy that replicates the staging smoke test against the production URL (with a dedicated test account that cannot touch real case data).
- Automate GitHub Release creation: `gh release create "$TAG" --generate-notes --target main`.
- Gate the workflow on the presence of a `v*` tag on `HEAD`; fail if tag is missing rather than silently pushing `latest`.

---

## 6.6 Kubernetes Manifests

Layout:

```
k8s/
├── base/
│   ├── namespace.yaml
│   ├── deployment-api-service.yaml
│   ├── service-api-service.yaml
│   ├── ingress.yaml
│   ├── cronjob-stuck-case-watchdog.yaml
│   ├── job-alembic-migrate.yaml            # applied separately by deploy workflow, not via kustomize
│   ├── secrets.yaml                        # template only; populated at deploy time
│   └── kustomization.yaml
└── overlays/
    ├── staging/
    │   └── kustomization.yaml              # namespace: verdictcouncil-staging
    └── production/
        └── kustomization.yaml              # namespace: verdictcouncil
```

Registered in the base kustomization: `namespace`, `deployment-api-service`, `service-api-service`, `ingress`, `cronjob-stuck-case-watchdog`. The Alembic job is applied separately by the deploy workflows so it can be sed'd to the current image tag.

### API Service Deployment (live)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-service
  namespace: verdictcouncil
  labels: {app: verdictcouncil, component: api-service}
spec:
  replicas: 1
  selector:
    matchLabels: {app: verdictcouncil, component: api-service}
  template:
    metadata:
      labels: {app: verdictcouncil, component: api-service}
    spec:
      containers:
        - name: api-service
          image: verdictcouncil:latest
          command: ["uvicorn"]
          args: ["src.api.app:app", "--host", "0.0.0.0", "--port", "8001"]
          ports:
            - containerPort: 8001
          envFrom:
            - secretRef: {name: verdictcouncil-secrets}
          resources:
            requests: {cpu: 250m, memory: 256Mi}
            limits:   {cpu: 500m, memory: 512Mi}
          livenessProbe:
            httpGet: {path: /metrics, port: 8001}
            initialDelaySeconds: 15
            periodSeconds: 30
            failureThreshold: 3
          readinessProbe:
            httpGet: {path: /metrics, port: 8001}
            initialDelaySeconds: 10
            periodSeconds: 10
            failureThreshold: 3
```

### `arq-worker` Deployment (live — `k8s/base/deployment-arq-worker.yaml`)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: arq-worker
  namespace: verdictcouncil
  labels: {app: verdictcouncil, component: arq-worker}
spec:
  replicas: 1
  selector:
    matchLabels: {app: verdictcouncil, component: arq-worker}
  template:
    metadata:
      labels: {app: verdictcouncil, component: arq-worker}
    spec:
      terminationGracePeriodSeconds: 900   # WorkerSettings.job_timeout
      containers:
        - name: arq-worker
          image: verdictcouncil:latest
          command: ["arq"]
          args: ["src.workers.worker_settings.WorkerSettings"]
          envFrom:
            - secretRef: {name: verdictcouncil-secrets}
          resources:
            requests: {cpu: 250m, memory: 256Mi}
            limits:   {cpu: 500m, memory: 512Mi}
          livenessProbe:
            exec:
              command: ["arq", "--check", "src.workers.worker_settings.WorkerSettings"]
            initialDelaySeconds: 30
            periodSeconds: 60
            failureThreshold: 3
```

Rationale for keeping the worker separate from the API:

- Pipeline runs spike CPU/memory while the API stays steady; scaling independently is cheaper.
- A stuck pipeline run must not take the API (and therefore the frontend) down.
- Logs are easier to reason about when job spans stay inside one container.

The compiled LangGraph `StateGraph` is held in process memory inside the worker; agent nodes execute as Python function calls, not over HTTP. There is no separate "Orchestrator" Deployment and no per-agent Deployment — earlier drafts of this document described both, but that nine-agent microservices split was decommissioned with the SAM/Solace removal. The graph topology is documented in `docs/architecture/02-system-architecture.md`.

### Build strategy

Both Deployments run the same `verdictcouncil:<tag>` image; the role is selected by `command`/`args`. This keeps the build matrix to one image and keeps image-layer cache hits high across roles. If we ever need per-agent slim images (e.g. only the `legal-knowledge` agent's deps in its container), the natural extension is a Docker matrix build — but that's not on the roadmap.

### Ingress (live)

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: verdictcouncil-ingress
  namespace: verdictcouncil
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"      # matches case_doc_max_upload_bytes
    nginx.ingress.kubernetes.io/proxy-buffering: "off"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"  # allows long SSE streams for in-flight pipelines
spec:
  ingressClassName: nginx
  tls:
    - hosts: [api.verdictcouncil.sg]
      secretName: verdictcouncil-tls
  rules:
    - host: api.verdictcouncil.sg
      http:
        paths:
          - path: /api/v1
            pathType: Prefix
            backend:
              service: {name: api-service, port: {number: 8001}}
```

### Stuck-Case Watchdog CronJob (live)

Runs every 5 minutes; moves cases stuck > 30 min into `failed_retryable`. Shares the same image + secret.

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: stuck-case-watchdog
  namespace: verdictcouncil
spec:
  schedule: "*/5 * * * *"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 5
  jobTemplate:
    spec:
      backoffLimit: 0
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: watchdog
              image: verdictcouncil:latest
              command: ["python", "-m", "src.services.stuck_case_watchdog"]
              envFrom:
                - secretRef: {name: verdictcouncil-secrets}
              resources:
                requests: {cpu: 50m, memory: 128Mi}
                limits:   {cpu: 100m, memory: 256Mi}
```

### Alembic Migrate Job (live)

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: alembic-migrate
  namespace: verdictcouncil
spec:
  ttlSecondsAfterFinished: 3600
  backoffLimit: 2
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: migrate
          image: verdictcouncil:latest
          command: ["alembic", "upgrade", "head"]
          envFrom:
            - secretRef: {name: verdictcouncil-secrets}
```

### Secret template (not committed with real values)

Production and staging both use `verdictcouncil-secrets` (populated by the deploy workflow). The template exists at `k8s/base/secrets.yaml` as a placeholder; values come from GitHub Secrets via `kubectl create secret --dry-run=client | kubectl apply -f -`.

### HorizontalPodAutoscaler (target)

Not in base yet. Target:

- **vc-api** scales on CPU + RPS (NGINX metrics via Prometheus adapter).
- **vc-orchestrator** scales on arq queue depth.
- **Per-agent HPAs** scale each agent on a queue-free signal — request concurrency at the Service + p95 latency. Frontier-tier agents scale more aggressively because they sit on the critical path.

---

## 6.7 Environment Promotion

```
  ┌─────────────────┐     ┌─────────────────┐     ┌──────────────────┐
  │   feat/<name>   │ ──▶│   development   │ ──▶│    release/...    │
  └─────────────────┘     └─────────────────┘     └──────────────────┘
                               │                          │
                     staging-deploy.yml (live)     production-deploy.yml
                               ▼                          ▼
                      DOKS verdictcouncil-staging   DOKS verdictcouncil
```

- Feature branches merge into `development` via PR; CI must pass.
- `development` → staging: push triggers `staging-deploy.yml` (today). Target is to move staging onto `release/**` so that `development` can absorb integration work without auto-deploying.
- `release/<context>/<tag>` → `main`: merge after staging QA passes. Push to `main` triggers `production-deploy.yml`.
- Hotfix branches: branch from `main`, PR into `main`, then back-port into `development`.

---

## 6.8 DigitalOcean Architecture

```mermaid
flowchart TB
    subgraph GH[GitHub]
        REPO[(Repo)]
        GHA[Actions Runners]
    end

    subgraph DOCR[DOCR]
        IMG[verdictcouncil image]
    end

    subgraph DOKS_STAGING[DOKS — verdictcouncil-staging]
        APIS[api-service]
        WRKS[arq-worker]
        WDS[stuck-case-watchdog CronJob]
        INGS[Ingress → staging-api.verdictcouncil.sg]
    end

    subgraph DOKS_PROD[DOKS — verdictcouncil]
        APIP[api-service]
        WRKP[arq-worker]
        WDP[stuck-case-watchdog CronJob]
        INGP[Ingress → api.verdictcouncil.sg]
    end

    subgraph Managed[DO Managed Services]
        PGP[(Postgres prod)]
        RDP[(Redis prod)]
        PGS[(Postgres staging)]
        RDS[(Redis staging)]
    end

    REPO -->|push development| GHA
    REPO -->|push main| GHA
    GHA -->|build + push| IMG
    GHA -->|kubectl apply + rollout| DOKS_STAGING
    GHA -->|kubectl apply + rollout| DOKS_PROD

    APIS --> PGS
    APIS --> RDS
    WRKS --> PGS
    WRKS --> RDS

    APIP --> PGP
    APIP --> RDP
    WRKP --> PGP
    WRKP --> RDP

    INGS --> APIS
    INGP --> APIP
```

---
