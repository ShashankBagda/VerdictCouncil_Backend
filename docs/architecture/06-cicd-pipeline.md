# Part 6: CI/CD Pipeline

## 6.1 Workflow Overview

Three GitHub Actions workflows aligned with the gitflow branching strategy:

| Workflow | Trigger | Purpose | Target |
|---|---|---|---|
| `ci.yml` | Push to `feat/*`, PR to `development` | Lint, test, security scan, build verification | — |
| `staging-deploy.yml` | Push to `release/*` | Build images, deploy to staging, smoke test | Staging cluster |
| `production-deploy.yml` | Push to `main` | Build release images, deploy to production, create GitHub Release | Production cluster |

## 6.2 CI Workflow

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches:
      - 'feat/**'
  pull_request:
    branches:
      - development

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

env:
  PYTHON_VERSION: '3.12'

jobs:
  lint-and-typecheck:
    name: Lint & Type Check
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install ruff mypy
          pip install -r requirements.txt

      - name: Run ruff linter
        run: ruff check . --output-format=github

      - name: Run ruff formatter check
        run: ruff format --check .

      - name: Run mypy type checking
        run: mypy src/ --ignore-missing-imports

  unit-tests:
    name: Unit Tests
    runs-on: ubuntu-latest
    needs: lint-and-typecheck
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install -r requirements-dev.txt

      - name: Run unit tests with coverage
        run: |
          pytest tests/unit/ \
            --cov=src \
            --cov-report=xml \
            --cov-report=term-missing \
            --cov-fail-under=80 \
            -v
        env:
          OPENAI_API_KEY: "sk-test-mock-key"

      - name: Upload coverage report
        uses: actions/upload-artifact@v4
        with:
          name: coverage-report
          path: coverage.xml

  integration-tests:
    name: Integration Tests
    runs-on: ubuntu-latest
    needs: unit-tests
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_DB: verdictcouncil_test
          POSTGRES_USER: vc_test
          POSTGRES_PASSWORD: test_password
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U vc_test"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
      redis:
        image: redis:7
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install -r requirements-dev.txt

      - name: Run database migrations
        run: python -m alembic upgrade head
        env:
          DATABASE_URL: "postgresql://vc_test:test_password@localhost:5432/verdictcouncil_test"

      - name: Run integration tests
        run: |
          pytest tests/integration/ \
            -v \
            --timeout=120
        env:
          DATABASE_URL: "postgresql://vc_test:test_password@localhost:5432/verdictcouncil_test"
          REDIS_URL: "redis://localhost:6379/0"
          OPENAI_API_KEY: "sk-test-mock-key"
          SOLACE_BROKER_URL: "tcp://localhost:55555"

  security-scan:
    name: Security Scan
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install pip-audit bandit
          pip install -r requirements.txt

      - name: Run pip-audit (dependency vulnerabilities)
        run: pip-audit --strict --desc

      - name: Run bandit (code security analysis)
        run: bandit -r src/ -f json -o bandit-report.json || true

      - name: Check bandit results
        run: |
          bandit -r src/ -ll -ii
        continue-on-error: false

      - name: Upload security report
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: security-reports
          path: bandit-report.json

  docker-build-test:
    name: Docker Build Verification
    runs-on: ubuntu-latest
    needs: [unit-tests, security-scan]
    strategy:
      matrix:
        agent:
          - web-gateway
          - case-processing
          - complexity-routing
          - evidence-analysis
          - fact-reconstruction
          - witness-analysis
          - legal-knowledge
          - argument-construction
          - deliberation
          - governance-verdict
          - layer2-aggregator
          - whatif-controller
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build image (no push)
        uses: docker/build-push-action@v5
        with:
          context: .
          file: ./docker/${{ matrix.agent }}/Dockerfile
          push: false
          tags: verdictcouncil/${{ matrix.agent }}:test
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

## 6.3 Docker Strategy

### Base Dockerfile

```dockerfile
# docker/base/Dockerfile
# Stage 1: Builder — install all dependencies
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime — minimal image
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy shared source code
COPY src/shared/ /app/src/shared/
COPY src/models/ /app/src/models/
COPY src/services/ /app/src/services/
COPY src/tools/ /app/src/tools/

# Non-root user for security
RUN groupadd -r vcagent && useradd -r -g vcagent vcagent
USER vcagent

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app
```

### Agent Dockerfile (example: case-processing)

```dockerfile
# docker/case-processing/Dockerfile
# Stage 1: Builder
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim AS runtime

WORKDIR /app

COPY --from=builder /install /usr/local

# Shared source
COPY src/shared/ /app/src/shared/
COPY src/models/ /app/src/models/
COPY src/services/ /app/src/services/
COPY src/tools/ /app/src/tools/

# Agent-specific code and config
COPY src/agents/case_processing/ /app/src/agents/case_processing/
COPY configs/agents/case_processing.yaml /app/configs/agent.yaml

RUN groupadd -r vcagent && useradd -r -g vcagent vcagent
USER vcagent

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

ENTRYPOINT ["python", "-m", "solace_agent_mesh.main", "--config", "/app/configs/agent.yaml"]
```

### Image Naming Convention

```
ghcr.io/verdictcouncil/web-gateway:{tag}
ghcr.io/verdictcouncil/case-processing:{tag}
ghcr.io/verdictcouncil/complexity-routing:{tag}
ghcr.io/verdictcouncil/evidence-analysis:{tag}
ghcr.io/verdictcouncil/fact-reconstruction:{tag}
ghcr.io/verdictcouncil/witness-analysis:{tag}
ghcr.io/verdictcouncil/legal-knowledge:{tag}
ghcr.io/verdictcouncil/argument-construction:{tag}
ghcr.io/verdictcouncil/deliberation:{tag}
ghcr.io/verdictcouncil/governance-verdict:{tag}
```

Tag formats:
- Feature builds: `feat-{branch}-{sha}` (never pushed)
- Staging: `rc-{sha}`
- Production: `v1.2.0` (semver from git tag)

## 6.4 Staging Deploy Workflow

```yaml
# .github/workflows/staging-deploy.yml
name: Staging Deploy

on:
  push:
    branches:
      - 'release/**'

env:
  REGISTRY: ghcr.io
  IMAGE_PREFIX: ghcr.io/${{ github.repository_owner }}

jobs:
  build-and-push:
    name: Build & Push Images
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    strategy:
      matrix:
        agent:
          - web-gateway
          - case-processing
          - complexity-routing
          - evidence-analysis
          - fact-reconstruction
          - witness-analysis
          - legal-knowledge
          - argument-construction
          - deliberation
          - governance-verdict
          - layer2-aggregator
          - whatif-controller
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract short SHA
        id: sha
        run: echo "short=$(git rev-parse --short HEAD)" >> "$GITHUB_OUTPUT"

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          file: ./docker/${{ matrix.agent }}/Dockerfile
          push: true
          tags: |
            ${{ env.IMAGE_PREFIX }}/${{ matrix.agent }}:rc-${{ steps.sha.outputs.short }}
            ${{ env.IMAGE_PREFIX }}/${{ matrix.agent }}:staging-latest
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy-staging:
    name: Deploy to Staging
    runs-on: ubuntu-latest
    needs: build-and-push
    environment: staging
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Configure kubectl
        uses: azure/setup-kubectl@v3
        with:
          version: 'v1.28.0'

      - name: Set kubeconfig
        run: |
          mkdir -p $HOME/.kube
          echo "${{ secrets.STAGING_KUBECONFIG }}" | base64 -d > $HOME/.kube/config

      - name: Extract short SHA
        id: sha
        run: echo "short=$(git rev-parse --short HEAD)" >> "$GITHUB_OUTPUT"

      - name: Update image tags in manifests
        run: |
          AGENTS=(
            web-gateway
            case-processing
            complexity-routing
            evidence-analysis
            fact-reconstruction
            witness-analysis
            legal-knowledge
            argument-construction
            deliberation
            governance-verdict
          )
          for agent in "${AGENTS[@]}"; do
            sed -i "s|image:.*${agent}:.*|image: ${{ env.IMAGE_PREFIX }}/${agent}:rc-${{ steps.sha.outputs.short }}|" \
              k8s/staging/${agent}-deployment.yaml
          done

      - name: Apply Kubernetes manifests
        run: |
          kubectl apply -f k8s/staging/ --namespace verdictcouncil-staging

      - name: Wait for rollout
        run: |
          DEPLOYMENTS=$(kubectl get deployments -n verdictcouncil-staging -o name)
          for deploy in $DEPLOYMENTS; do
            kubectl rollout status "$deploy" -n verdictcouncil-staging --timeout=300s
          done

  smoke-test:
    name: Smoke Test
    runs-on: ubuntu-latest
    needs: deploy-staging
    environment: staging
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Wait for services to stabilise
        run: sleep 15

      - name: Health check all agents
        run: |
          STAGING_URL="${{ secrets.STAGING_URL }}"
          response=$(curl -s -o /dev/null -w "%{http_code}" "${STAGING_URL}/health")
          if [ "$response" != "200" ]; then
            echo "Gateway health check failed with status $response"
            exit 1
          fi
          echo "Gateway health check passed"

      - name: Submit test case
        run: |
          STAGING_URL="${{ secrets.STAGING_URL }}"

          # Authenticate
          TOKEN=$(curl -s -X POST "${STAGING_URL}/auth/login" \
            -H "Content-Type: application/json" \
            -d '{"email":"test-judge@verdictcouncil.sg","password":"${{ secrets.STAGING_TEST_PASSWORD }}"}' \
            | jq -r '.token')

          # Submit test case
          CASE_ID=$(curl -s -X POST "${STAGING_URL}/api/v1/cases" \
            -H "Authorization: Bearer ${TOKEN}" \
            -F "documents=@tests/fixtures/test_case.pdf" \
            -F "domain=small_claims" \
            | jq -r '.case_id')

          echo "Test case submitted: ${CASE_ID}"
          echo "case_id=${CASE_ID}" >> "$GITHUB_ENV"

      - name: Wait for pipeline completion
        run: |
          STAGING_URL="${{ secrets.STAGING_URL }}"
          MAX_WAIT=300
          ELAPSED=0
          while [ $ELAPSED -lt $MAX_WAIT ]; do
            STATUS=$(curl -s "${STAGING_URL}/api/v1/cases/${case_id}" \
              -H "Authorization: Bearer ${TOKEN}" \
              | jq -r '.status')
            if [ "$STATUS" = "ready_for_review" ] || [ "$STATUS" = "escalated" ]; then
              echo "Pipeline completed with status: ${STATUS}"
              exit 0
            fi
            sleep 10
            ELAPSED=$((ELAPSED + 10))
            echo "Waiting... (${ELAPSED}s / ${MAX_WAIT}s) — current status: ${STATUS}"
          done
          echo "Pipeline did not complete within ${MAX_WAIT}s"
          exit 1

  notify:
    name: Notify
    runs-on: ubuntu-latest
    needs: [deploy-staging, smoke-test]
    if: always()
    steps:
      - name: Post deployment status
        run: |
          if [ "${{ needs.smoke-test.result }}" = "success" ]; then
            echo "Staging deployment successful"
          else
            echo "Staging deployment failed — check workflow logs"
            exit 1
          fi
```

## 6.5 Production Deploy Workflow

```yaml
# .github/workflows/production-deploy.yml
name: Production Deploy

on:
  push:
    branches:
      - main

env:
  REGISTRY: ghcr.io
  IMAGE_PREFIX: ghcr.io/${{ github.repository_owner }}

jobs:
  build-and-push:
    name: Build & Push Release Images
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    outputs:
      version: ${{ steps.version.outputs.tag }}
    strategy:
      matrix:
        agent:
          - web-gateway
          - case-processing
          - complexity-routing
          - evidence-analysis
          - fact-reconstruction
          - witness-analysis
          - legal-knowledge
          - argument-construction
          - deliberation
          - governance-verdict
          - layer2-aggregator
          - whatif-controller
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Get version from git tag
        id: version
        run: |
          TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")
          echo "tag=${TAG}" >> "$GITHUB_OUTPUT"
          echo "Building version: ${TAG}"

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          file: ./docker/${{ matrix.agent }}/Dockerfile
          push: true
          tags: |
            ${{ env.IMAGE_PREFIX }}/${{ matrix.agent }}:${{ steps.version.outputs.tag }}
            ${{ env.IMAGE_PREFIX }}/${{ matrix.agent }}:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy-production:
    name: Deploy to Production
    runs-on: ubuntu-latest
    needs: build-and-push
    environment: production
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Configure kubectl
        uses: azure/setup-kubectl@v3
        with:
          version: 'v1.28.0'

      - name: Set kubeconfig
        run: |
          mkdir -p $HOME/.kube
          echo "${{ secrets.PRODUCTION_KUBECONFIG }}" | base64 -d > $HOME/.kube/config

      - name: Get version
        id: version
        run: |
          TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")
          echo "tag=${TAG}" >> "$GITHUB_OUTPUT"

      - name: Update image tags in manifests
        run: |
          AGENTS=(
            web-gateway
            case-processing
            complexity-routing
            evidence-analysis
            fact-reconstruction
            witness-analysis
            legal-knowledge
            argument-construction
            deliberation
            governance-verdict
          )
          for agent in "${AGENTS[@]}"; do
            sed -i "s|image:.*${agent}:.*|image: ${{ env.IMAGE_PREFIX }}/${agent}:${{ steps.version.outputs.tag }}|" \
              k8s/production/${agent}-deployment.yaml
          done

      - name: Apply Kubernetes manifests (rolling update)
        run: |
          kubectl apply -f k8s/production/ --namespace verdictcouncil

      - name: Wait for rollout
        run: |
          DEPLOYMENTS=$(kubectl get deployments -n verdictcouncil -o name)
          for deploy in $DEPLOYMENTS; do
            kubectl rollout status "$deploy" -n verdictcouncil --timeout=600s
          done

  verify:
    name: Production Verification
    runs-on: ubuntu-latest
    needs: deploy-production
    environment: production
    steps:
      - name: Health check all pods
        run: |
          PROD_URL="${{ secrets.PRODUCTION_URL }}"
          response=$(curl -s -o /dev/null -w "%{http_code}" "${PROD_URL}/health")
          if [ "$response" != "200" ]; then
            echo "Production health check failed with status $response"
            exit 1
          fi
          echo "Production health check passed"

      - name: Canary test case
        run: |
          PROD_URL="${{ secrets.PRODUCTION_URL }}"

          TOKEN=$(curl -s -X POST "${PROD_URL}/auth/login" \
            -H "Content-Type: application/json" \
            -d '{"email":"canary@verdictcouncil.sg","password":"${{ secrets.CANARY_TEST_PASSWORD }}"}' \
            | jq -r '.token')

          CASE_ID=$(curl -s -X POST "${PROD_URL}/api/v1/cases" \
            -H "Authorization: Bearer ${TOKEN}" \
            -F "documents=@tests/fixtures/canary_case.pdf" \
            -F "domain=small_claims" \
            | jq -r '.case_id')

          echo "Canary case submitted: ${CASE_ID}"

          MAX_WAIT=300
          ELAPSED=0
          while [ $ELAPSED -lt $MAX_WAIT ]; do
            STATUS=$(curl -s "${PROD_URL}/api/v1/cases/${CASE_ID}" \
              -H "Authorization: Bearer ${TOKEN}" \
              | jq -r '.status')
            if [ "$STATUS" = "ready_for_review" ] || [ "$STATUS" = "escalated" ]; then
              echo "Canary passed with status: ${STATUS}"
              exit 0
            fi
            sleep 10
            ELAPSED=$((ELAPSED + 10))
          done
          echo "Canary test did not complete within ${MAX_WAIT}s"
          exit 1

  create-release:
    name: Create GitHub Release
    runs-on: ubuntu-latest
    needs: verify
    permissions:
      contents: write
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Get version
        id: version
        run: |
          TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")
          echo "tag=${TAG}" >> "$GITHUB_OUTPUT"

      - name: Create GitHub Release
        run: |
          gh release create "${{ steps.version.outputs.tag }}" \
            --title "${{ steps.version.outputs.tag }}" \
            --generate-notes \
            --target main
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

## 6.6 Kubernetes Manifest Examples

### Agent Deployment + Service (Template)

```yaml
# k8s/base/agent-deployment.yaml (example: case-processing)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: case-processing
  namespace: verdictcouncil
  labels:
    app: case-processing
    component: agent
spec:
  replicas: 1
  selector:
    matchLabels:
      app: case-processing
  template:
    metadata:
      labels:
        app: case-processing
        component: agent
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "9090"
    spec:
      serviceAccountName: verdictcouncil-agent
      containers:
        - name: case-processing
          image: ghcr.io/verdictcouncil/case-processing:latest
          ports:
            - containerPort: 9090
              name: metrics
          envFrom:
            - configMapRef:
                name: agent-common-config
            - secretRef:
                name: verdictcouncil-secrets
          env:
            - name: AGENT_NAME
              value: "case-processing"
          resources:
            requests:
              cpu: 250m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          readinessProbe:
            httpGet:
              path: /health
              port: 9090
            initialDelaySeconds: 10
            periodSeconds: 15
          livenessProbe:
            httpGet:
              path: /health
              port: 9090
            initialDelaySeconds: 30
            periodSeconds: 30
      restartPolicy: Always
---
apiVersion: v1
kind: Service
metadata:
  name: case-processing-svc
  namespace: verdictcouncil
  labels:
    app: case-processing
spec:
  type: ClusterIP
  selector:
    app: case-processing
  ports:
    - port: 9090
      targetPort: 9090
      name: metrics
```

### PostgreSQL StatefulSet

```yaml
# k8s/base/postgresql-statefulset.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgresql
  namespace: verdictcouncil
  labels:
    app: postgresql
    component: database
spec:
  serviceName: postgresql-svc
  replicas: 1
  selector:
    matchLabels:
      app: postgresql
  template:
    metadata:
      labels:
        app: postgresql
        component: database
    spec:
      containers:
        - name: postgresql
          image: postgres:16
          ports:
            - containerPort: 5432
              name: postgres
          env:
            - name: POSTGRES_DB
              value: verdictcouncil
            - name: POSTGRES_USER
              valueFrom:
                secretKeyRef:
                  name: verdictcouncil-secrets
                  key: POSTGRES_USER
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: verdictcouncil-secrets
                  key: POSTGRES_PASSWORD
            - name: PGDATA
              value: /var/lib/postgresql/data/pgdata
          resources:
            requests:
              cpu: 500m
              memory: 512Mi
            limits:
              cpu: "1"
              memory: 1Gi
          volumeMounts:
            - name: postgresql-data
              mountPath: /var/lib/postgresql/data
          readinessProbe:
            exec:
              command:
                - pg_isready
                - -U
                - $(POSTGRES_USER)
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            exec:
              command:
                - pg_isready
                - -U
                - $(POSTGRES_USER)
            initialDelaySeconds: 30
            periodSeconds: 30
  volumeClaimTemplates:
    - metadata:
        name: postgresql-data
      spec:
        accessModes:
          - ReadWriteOnce
        resources:
          requests:
            storage: 50Gi
---
apiVersion: v1
kind: Service
metadata:
  name: postgresql-svc
  namespace: verdictcouncil
  labels:
    app: postgresql
spec:
  type: ClusterIP
  selector:
    app: postgresql
  ports:
    - port: 5432
      targetPort: 5432
      name: postgres
```

### Redis StatefulSet

```yaml
# k8s/base/redis-statefulset.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis
  namespace: verdictcouncil
  labels:
    app: redis
    component: cache
spec:
  serviceName: redis-svc
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
        component: cache
    spec:
      containers:
        - name: redis
          image: redis:7
          ports:
            - containerPort: 6379
              name: redis
          command:
            - redis-server
            - --maxmemory
            - 256mb
            - --maxmemory-policy
            - allkeys-lru
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 250m
              memory: 300Mi
          readinessProbe:
            exec:
              command:
                - redis-cli
                - ping
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            exec:
              command:
                - redis-cli
                - ping
            initialDelaySeconds: 15
            periodSeconds: 20
          volumeMounts:
            - name: redis-data
              mountPath: /data
  volumeClaimTemplates:
    - metadata:
        name: redis-data
      spec:
        accessModes:
          - ReadWriteOnce
        resources:
          requests:
            storage: 5Gi
---
apiVersion: v1
kind: Service
metadata:
  name: redis-svc
  namespace: verdictcouncil
  labels:
    app: redis
spec:
  type: ClusterIP
  selector:
    app: redis
  ports:
    - port: 6379
      targetPort: 6379
      name: redis
```

### ConfigMap for Agent Configuration

```yaml
# k8s/base/agent-configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: agent-common-config
  namespace: verdictcouncil
  labels:
    component: config
data:
  SOLACE_BROKER_URL: "tcp://solace-broker-svc:55555"
  SOLACE_BROKER_VPN: "verdictcouncil"
  # NOTE: DATABASE_URL is NOT set here. Kubernetes ConfigMaps do not perform
  # $(VAR) interpolation — those are literal strings, not resolved references.
  # DATABASE_URL is constructed at container startup via an entrypoint script
  # that reads POSTGRES_USER and POSTGRES_PASSWORD from the Secret and builds
  # the connection string dynamically. See the entrypoint.sh in each agent's
  # Docker image.
  POSTGRES_HOST: "postgresql-svc"
  POSTGRES_PORT: "5432"
  POSTGRES_DB: "verdictcouncil"
  REDIS_URL: "redis://redis-svc:6379/0"
  FASTAPI_HOST: "0.0.0.0"
  FASTAPI_PORT: "8000"
  LOG_LEVEL: "INFO"
  NAMESPACE: "verdictcouncil"
  PRECEDENT_CACHE_TTL_SECONDS: "86400"
  JUDICIARY_BASE_URL: "https://www.judiciary.gov.sg"
  PAIR_BASE_URL: "https://search.pair.gov.sg"
```

### Secret for Credentials

```yaml
# k8s/base/secrets.yaml (values are base64-encoded placeholders)
apiVersion: v1
kind: Secret
metadata:
  name: verdictcouncil-secrets
  namespace: verdictcouncil
  labels:
    component: secrets
type: Opaque
data:
  OPENAI_API_KEY: "BASE64_ENCODED_VALUE"
  SOLACE_BROKER_USERNAME: "BASE64_ENCODED_VALUE"
  SOLACE_BROKER_PASSWORD: "BASE64_ENCODED_VALUE"
  POSTGRES_USER: "BASE64_ENCODED_VALUE"
  POSTGRES_PASSWORD: "BASE64_ENCODED_VALUE"
  JWT_SECRET: "BASE64_ENCODED_VALUE"
```

### HorizontalPodAutoscaler for Web Gateway

```yaml
# k8s/base/web-gateway-hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: web-gateway-hpa
  namespace: verdictcouncil
  labels:
    app: web-gateway
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: web-gateway
  minReplicas: 2
  maxReplicas: 5
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 1
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
        - type: Pods
          value: 1
          periodSeconds: 120
```

### Ingress

```yaml
# k8s/base/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: verdictcouncil-ingress
  namespace: verdictcouncil
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "300"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "300"
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - api.verdictcouncil.sg
      secretName: verdictcouncil-tls
  rules:
    - host: api.verdictcouncil.sg
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: web-gateway-svc
                port:
                  number: 8000
```

## 6.7 Environment Promotion Diagram

```mermaid
flowchart LR
    subgraph Feature["Feature Development"]
        FEAT["feat/* branch"]
    end

    subgraph CI["CI Pipeline"]
        LINT["Lint & Type Check"]
        UNIT["Unit Tests"]
        INTEG["Integration Tests"]
        SEC["Security Scan"]
        BUILD["Docker Build Test"]
    end

    subgraph Integration["Integration"]
        DEV["development branch"]
    end

    subgraph Staging["Staging Pipeline"]
        REL["release/* branch"]
        SBUILD["Build & Push<br/>rc-{sha} tags"]
        SDEPLOY["Deploy Staging<br/>K8s namespace:<br/>verdictcouncil-staging"]
        SMOKE["Smoke Test"]
    end

    subgraph Production["Production Pipeline"]
        MAIN["main branch"]
        PBUILD["Build & Push<br/>v{semver} tags"]
        PDEPLOY["Deploy Production<br/>K8s namespace:<br/>verdictcouncil"]
        VERIFY["Verify & Canary"]
        RELEASE["GitHub Release<br/>+ Auto Notes"]
    end

    FEAT -->|"push / PR"| LINT
    LINT --> UNIT
    UNIT --> INTEG
    LINT --> SEC
    UNIT --> BUILD
    SEC --> BUILD

    BUILD -->|"PR merge"| DEV

    DEV -->|"merge when stable"| REL
    REL --> SBUILD
    SBUILD --> SDEPLOY
    SDEPLOY --> SMOKE

    SMOKE -->|"QA approved<br/>merge"| MAIN
    MAIN --> PBUILD
    PBUILD --> PDEPLOY
    PDEPLOY --> VERIFY
    VERIFY --> RELEASE

    style FEAT fill:#4a9eff,color:#fff
    style DEV fill:#ffa500,color:#fff
    style REL fill:#ff6b6b,color:#fff
    style MAIN fill:#2ecc71,color:#fff
    style RELEASE fill:#2ecc71,color:#fff
```

---

