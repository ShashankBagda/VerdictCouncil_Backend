# Local Kubernetes Overlay (kind)

Runs the full backend stack on a local [kind](https://kind.sigs.k8s.io/) cluster.
Postgres and Redis deploy as in-cluster pods (no DO managed services required).

## Prerequisites

```bash
brew install kind kubectl
```

## Quick start

```bash
# 1. Create cluster
kind create cluster --name verdictcouncil-local

# 2. Build and side-load the backend image
docker build -t verdictcouncil:latest ../../..
kind load docker-image verdictcouncil:latest --name verdictcouncil-local

# 3. Apply secrets (OPENAI_API_KEY required at runtime)
export OPENAI_API_KEY=sk-...
envsubst < secrets.yaml | kubectl --context kind-verdictcouncil-local apply -f -

# 4. Apply the rest of the stack
kubectl --context kind-verdictcouncil-local apply -k .

# 5. Wait for Postgres and Redis to be ready, then access the API
kubectl --context kind-verdictcouncil-local port-forward \
  -n verdictcouncil-local svc/api-service 8001:8001
# → http://localhost:8001/healthz
```

## What this overlay changes from base

| Setting | Base (staging/prod) | Local |
|---------|---------------------|-------|
| Postgres | DO Managed Postgres 16 | `postgres:16-alpine` pod (emptyDir) |
| Redis | DO Managed Valkey | `redis:7-alpine` pod |
| Secrets | DOKS Secret via deploy workflow | `envsubst` from shell env |
| imagePullPolicy | IfNotPresent (DOCR) | `IfNotPresent` (kind-loaded) |
| Ingress | nginx-ingress + TLS | applied but no-op (no controller) |
| LangGraph checkpointer | postgres | disabled (no migrations run) |

## Tear down

```bash
kind delete cluster --name verdictcouncil-local
```
