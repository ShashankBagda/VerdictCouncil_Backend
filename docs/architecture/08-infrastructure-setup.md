# Part 8: Infrastructure Setup (DigitalOcean)

This guide covers one-time provisioning of all DigitalOcean resources needed to run VerdictCouncil in staging and production. After completing these steps, the CI/CD workflows in [Part 6](06-cicd-pipeline.md) will handle all ongoing deployments.

> **For local development**, see [Part 9: Local Development](09-local-development.md) — covers docker-compose, native Python agents, and local K8s via kind.

---

## 8.1 Prerequisites

| Tool | Version | Install |
|---|---|---|
| `doctl` | 1.110+ | `brew install doctl` or [docs](https://docs.digitalocean.com/reference/doctl/how-to/install/) |
| `kubectl` | 1.31+ | `brew install kubectl` |
| `helm` | 3.x | `brew install helm` |
| `docker` | 24+ | [Docker Desktop](https://www.docker.com/products/docker-desktop/) |
| `gh` | 2.x | `brew install gh` (for GitHub Actions secrets) |

### Authenticate doctl

```bash
# Create a personal access token at https://cloud.digitalocean.com/account/api/tokens
# Scopes: read, write
doctl auth init --context verdictcouncil
doctl auth switch --context verdictcouncil

# Verify
doctl account get
```

---

## 8.2 Container Registry (DOCR)

Create a private container registry to store all agent images:

```bash
# Create registry (name must be globally unique)
doctl registry create verdictcouncil --region sgp1 --subscription-tier professional

# Verify
doctl registry get
```

**Tier selection:**

| Tier | Storage | Included Repos | Price | Use Case |
|---|---|---|---|---|
| Starter | 500 MB | 1 | Free | Not sufficient (12 images) |
| Basic | 5 GB | Unlimited | $5/mo | Development/testing |
| Professional | 50 GB | Unlimited | $12/mo | Production (recommended) |

---

## 8.3 Kubernetes Clusters (DOKS)

Create two DOKS clusters: one for staging, one for production.

### 8.3.1 Staging Cluster

```bash
doctl kubernetes cluster create vc-staging \
  --region sgp1 \
  --version 1.31.6-do.0 \
  --node-pool "name=staging-pool;size=s-4vcpu-8gb;count=2;auto-scale=true;min-nodes=2;max-nodes=3" \
  --vpc-uuid <your-vpc-uuid> \
  --set-current-context

# Save cluster ID for GitHub Actions
doctl kubernetes cluster list --format ID,Name
```

### 8.3.2 Production Cluster

```bash
doctl kubernetes cluster create vc-production \
  --region sgp1 \
  --version 1.31.6-do.0 \
  --node-pool "name=production-pool;size=s-4vcpu-8gb;count=3;auto-scale=true;min-nodes=3;max-nodes=5" \
  --vpc-uuid <your-vpc-uuid> \
  --set-current-context

# Save cluster ID for GitHub Actions
doctl kubernetes cluster list --format ID,Name
```

### 8.3.3 Node Pool Sizing

The system runs 13 pods (12 Deployments + 1 StatefulSet). Resource requirements:

| Component | CPU Request | Memory Request | Count |
|---|---|---|---|
| web-gateway | 250m | 256Mi | 2 (HPA min) |
| Agent pods (×10) | 250m | 256Mi | 10 |
| layer2-aggregator | 250m | 256Mi | 1 |
| whatif-controller | 250m | 256Mi | 1 |
| solace-broker | 500m | 1Gi | 1 |
| nginx-ingress | 100m | 128Mi | 1 |
| **Total** | **~4.35 vCPU** | **~5.4 Gi** | **16 pods** |

**Recommended node size:** `s-4vcpu-8gb` ($48/mo each)

| Environment | Nodes | Monthly Cost | Headroom |
|---|---|---|---|
| Staging | 2 | $96 | Minimal — sufficient for testing |
| Production | 3 | $144 | Room for HPA scale-up and rolling updates |

### 8.3.4 Connect DOCR to DOKS

Allow DOKS clusters to pull images from DOCR without secrets:

```bash
# Staging
doctl kubernetes cluster registry add <staging-cluster-id>

# Production
doctl kubernetes cluster registry add <production-cluster-id>
```

---

## 8.4 Managed PostgreSQL

```bash
doctl databases create vc-postgresql \
  --engine pg \
  --version 16 \
  --region sgp1 \
  --size db-s-2vcpu-4gb \
  --num-nodes 1 \
  --private-network-uuid <your-vpc-uuid>
```

### Configuration

| Setting | Value | Notes |
|---|---|---|
| Engine | PostgreSQL 16 | Matches CI test services |
| Size | `db-s-2vcpu-4gb` ($60/mo) | 2 vCPU, 4 GB RAM, 38 GB SSD |
| Nodes | 1 (standby available at +$60/mo) | Add standby for production HA |
| Region | `sgp1` (Singapore) | Same region as DOKS for low latency |
| VPC | Same VPC as DOKS | Private networking, no public exposure |

### Post-Creation Setup

```bash
# Get connection details
doctl databases connection vc-postgresql --format Host,Port,User,Password,Database,URI

# Create application database and user
doctl databases db create <db-cluster-id> verdictcouncil

doctl databases user create <db-cluster-id> vc_app

# Restrict trusted sources to DOKS clusters only
doctl databases firewalls append <db-cluster-id> \
  --rule k8s:<staging-cluster-id> \
  --rule k8s:<production-cluster-id>
```

### Sizing Guide

| Size | vCPU | RAM | Storage | Price | Use Case |
|---|---|---|---|---|---|
| `db-s-1vcpu-1gb` | 1 | 1 GB | 10 GB | $15/mo | Development only |
| `db-s-1vcpu-2gb` | 1 | 2 GB | 25 GB | $30/mo | Staging |
| `db-s-2vcpu-4gb` | 2 | 4 GB | 38 GB | $60/mo | Production (recommended) |
| `db-s-4vcpu-8gb` | 4 | 8 GB | 115 GB | $120/mo | High volume (500+ cases/mo) |

---

## 8.5 Managed Redis

```bash
doctl databases create vc-redis \
  --engine redis \
  --version 7 \
  --region sgp1 \
  --size db-s-1vcpu-2gb \
  --num-nodes 1 \
  --private-network-uuid <your-vpc-uuid>
```

### Configuration

| Setting | Value | Notes |
|---|---|---|
| Engine | Redis 7 | TLS enabled by default |
| Size | `db-s-1vcpu-2gb` ($15/mo) | 1 vCPU, 2 GB RAM |
| Eviction Policy | `allkeys-lru` | Set via DO console or API |
| Region | `sgp1` | Same VPC as DOKS |

### Post-Creation Setup

```bash
# Get connection details
doctl databases connection vc-redis --format Host,Port,User,Password,URI

# Restrict to DOKS clusters
doctl databases firewalls append <redis-cluster-id> \
  --rule k8s:<staging-cluster-id> \
  --rule k8s:<production-cluster-id>

# Set eviction policy
doctl databases configuration update <redis-cluster-id> \
  --config-json '{"redis_maxmemory_policy": "allkeys-lru"}'
```

---

## 8.6 VPC & Networking

All resources should be in the same VPC for private networking:

```bash
# Create VPC (if not using the default)
doctl vpcs create \
  --name verdictcouncil-vpc \
  --region sgp1 \
  --ip-range 10.116.0.0/20

# List VPCs to get UUID
doctl vpcs list --format ID,Name,IPRange
```

### Network Topology

```
┌─────────────────────────────────────────────────────────┐
│  DO VPC: verdictcouncil-vpc (10.116.0.0/20)             │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ DOKS Staging │  │ DOKS Prod    │  │ Managed PG   │  │
│  │ 10.116.0.x   │  │ 10.116.1.x   │  │ 10.116.2.x   │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│                                                         │
│  ┌──────────────┐                                       │
│  │ Managed Redis│                                       │
│  │ 10.116.3.x   │                                       │
│  └──────────────┘                                       │
└─────────────────────────────────────────────────────────┘
              │
     DO Load Balancer (public IP)
              │
         Internet
```

---

## 8.7 DNS Configuration

Point your domain to the DO Load Balancer provisioned by DOKS:

```bash
# Get the Load Balancer external IP (created by ingress)
kubectl get svc -n ingress-nginx ingress-nginx-controller -o jsonpath='{.status.loadBalancer.ingress[0].ip}'

# If using DO DNS:
doctl compute domain create verdictcouncil.sg
doctl compute domain records create verdictcouncil.sg \
  --record-type A \
  --record-name api \
  --record-data <load-balancer-ip> \
  --record-ttl 300

# Staging subdomain
doctl compute domain records create verdictcouncil.sg \
  --record-type A \
  --record-name staging-api \
  --record-data <staging-load-balancer-ip> \
  --record-ttl 300
```

---

## 8.8 Ingress Controller & TLS

Install nginx-ingress and cert-manager on both clusters:

```bash
# Switch to target cluster
doctl kubernetes cluster kubeconfig save <cluster-id>

# Install nginx-ingress controller
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update
helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace \
  --set controller.publishService.enabled=true \
  --set controller.service.annotations."service\.beta\.kubernetes\.io/do-loadbalancer-protocol"=http \
  --set controller.service.annotations."service\.beta\.kubernetes\.io/do-loadbalancer-size-unit"=1

# Install cert-manager for Let's Encrypt TLS
helm repo add jetstack https://charts.jetstack.io
helm repo update
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --create-namespace \
  --set crds.enabled=true

# Create ClusterIssuer for Let's Encrypt
kubectl apply -f - <<EOF
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: admin@verdictcouncil.sg
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
      - http01:
          ingress:
            class: nginx
EOF
```

---

## 8.9 Kubernetes Namespaces & Secrets

### Create Namespaces

```bash
# Staging cluster
doctl kubernetes cluster kubeconfig save <staging-cluster-id>
kubectl create namespace verdictcouncil-staging

# Production cluster
doctl kubernetes cluster kubeconfig save <production-cluster-id>
kubectl create namespace verdictcouncil
```

### Create Application Secrets

Populate secrets in each cluster using values from `doctl databases connection`:

```bash
# Get managed service connection details
PG_URI=$(doctl databases connection <pg-cluster-id> --format URI --no-header)
REDIS_URI=$(doctl databases connection <redis-cluster-id> --format URI --no-header)

# Create secrets (production example)
kubectl create secret generic verdictcouncil-secrets \
  --namespace verdictcouncil \
  --from-literal=OPENAI_API_KEY="sk-proj-..." \
  --from-literal=SOLACE_BROKER_USERNAME="vc-agent" \
  --from-literal=SOLACE_BROKER_PASSWORD="<solace-password>" \
  --from-literal=POSTGRES_USER="vc_app" \
  --from-literal=POSTGRES_PASSWORD="<pg-password>" \
  --from-literal=JWT_SECRET="<256-bit-secret>" \
  --from-literal=DATABASE_URL="${PG_URI}" \
  --from-literal=REDIS_URL="${REDIS_URI}"
```

---

## 8.10 GitHub Actions Secrets

Set the GitHub repository secrets used by CI/CD workflows:

```bash
# DigitalOcean authentication
gh secret set DIGITALOCEAN_ACCESS_TOKEN --body "<your-do-api-token>"
gh secret set DOCR_REGISTRY_NAME --body "verdictcouncil"

# Cluster IDs
gh secret set DOKS_CLUSTER_ID_STAGING --body "<staging-cluster-id>"
gh secret set DOKS_CLUSTER_ID_PRODUCTION --body "<production-cluster-id>"

# Endpoints
gh secret set STAGING_URL --body "https://staging-api.verdictcouncil.sg"
gh secret set PRODUCTION_URL --body "https://api.verdictcouncil.sg"

# Test credentials
gh secret set STAGING_TEST_PASSWORD --body "<test-password>"
gh secret set CANARY_TEST_PASSWORD --body "<canary-password>"
```

### GitHub Environments

Create `staging` and `production` environments in the repository settings:

1. Go to **Settings → Environments**
2. Create `staging` environment (no protection rules needed)
3. Create `production` environment:
   - Enable **Required reviewers** — add at least one team member
   - Enable **Wait timer** — 5 minutes (optional, allows rollback window)
   - Restrict to `main` branch only

---

## 8.11 DO Spaces (Backups & Artifacts)

```bash
# Create Spaces bucket for backups
doctl spaces create verdictcouncil-backups \
  --region sgp1

# Create Spaces access keys (for backup scripts)
doctl spaces keys create --name vc-backup-key
```

### Automated PostgreSQL Backups

DO Managed PostgreSQL includes automatic daily backups with 7-day retention. For additional backup strategies:

```bash
# Manual backup export (for archival to Spaces)
doctl databases backups list <pg-cluster-id>

# Restore from backup
doctl databases backups restore <pg-cluster-id> --backup-id <backup-id>
```

---

## 8.12 Monitoring & Alerting

### DigitalOcean Monitoring

DO provides built-in monitoring for all managed services:

- **DOKS:** CPU, memory, disk usage per node; pod-level metrics
- **Managed PostgreSQL:** connections, queries/sec, replication lag, disk usage
- **Managed Redis:** memory usage, connections, hit/miss ratio, evictions
- **Load Balancer:** request rate, response codes, latency

### Prometheus + Grafana (Application-Level)

Install the DO Kubernetes Monitoring Stack for deeper observability:

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --set grafana.adminPassword="<grafana-password>" \
  --set prometheus.prometheusSpec.retention=30d \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.storageClassName=do-block-storage \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage=20Gi
```

### Alerts to Configure

| Alert | Condition | Severity |
|---|---|---|
| Pod CrashLoopBackOff | Any agent pod restarting > 3 times in 5m | Critical |
| Pipeline Timeout | Case stuck in PROCESSING > 10m | Warning |
| Database Connections | > 80% of max connections | Warning |
| Redis Memory | > 80% of maxmemory | Warning |
| LLM API Errors | > 5% error rate over 5m | Critical |
| Certificate Expiry | TLS cert expires in < 14 days | Warning |

---

## 8.13 Provisioning Checklist

Complete these steps in order:

- [ ] Install prerequisites (`doctl`, `kubectl`, `helm`, `docker`, `gh`)
- [ ] Authenticate `doctl` with API token
- [ ] Create VPC in `sgp1` region
- [ ] Create DOCR registry (Professional tier)
- [ ] Create DOKS staging cluster (2 nodes)
- [ ] Create DOKS production cluster (3 nodes)
- [ ] Connect DOCR to both DOKS clusters
- [ ] Create Managed PostgreSQL (same VPC)
- [ ] Create Managed Redis (same VPC)
- [ ] Set database firewall rules (restrict to DOKS clusters)
- [ ] Create application database and user in PostgreSQL
- [ ] Install nginx-ingress on both clusters
- [ ] Install cert-manager on both clusters
- [ ] Create ClusterIssuer for Let's Encrypt
- [ ] Create Kubernetes namespaces
- [ ] Create Kubernetes secrets in both clusters
- [ ] Configure DNS (A records for api / staging-api)
- [ ] Set GitHub Actions secrets
- [ ] Create GitHub environments (staging, production)
- [ ] Create DO Spaces bucket for backups
- [ ] Install Prometheus + Grafana monitoring stack
- [ ] Verify: push a test image to DOCR and pull from DOKS

---

## 8.14 Monthly Cost Estimate

| Resource | Staging | Production | Notes |
|---|---|---|---|
| DOKS Cluster (control plane) | Free | Free | DO does not charge for the control plane |
| DOKS Nodes | $96 (2× s-4vcpu-8gb) | $144 (3× s-4vcpu-8gb) | Auto-scale up adds $48/node |
| Managed PostgreSQL | $30 (s-1vcpu-2gb) | $60 (s-2vcpu-4gb) | +$60 for standby node |
| Managed Redis | $15 (s-1vcpu-1gb) | $15 (s-1vcpu-2gb) | Sufficient for caching workload |
| DOCR (Professional) | — | $12 | Shared across environments |
| DO Load Balancer | $12 | $12 | 1 per cluster (auto-provisioned) |
| DO Spaces | — | $5 | 250 GB included |
| Block Storage (Solace PVC) | $4 (20 GB) | $4 (20 GB) | $0.10/GB/mo |
| **Total** | **~$157/mo** | **~$252/mo** | |
| **Combined** | | **~$409/mo** | Excluding LLM API costs |

> LLM API costs are usage-based and estimated at $0.40–$0.55 per case. See [Appendix A](appendices.md#appendix-a-cost-model) for per-case breakdown and monthly projections.

---
