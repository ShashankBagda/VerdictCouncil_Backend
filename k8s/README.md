# Kubernetes manifests — live deploy target

These are the **production deployment manifests.** A single CI workflow
`../.github/workflows/deploy.yml` (branch determines env: `development`
→ staging, `main` → production) builds the Docker image, pushes it to
DigitalOcean Container Registry, and then applies the kustomize overlay
for the matching env (`k8s/overlays/staging/` or `k8s/overlays/production/`)
against a DigitalOcean Kubernetes (DOKS) cluster.

## Layout

```
k8s/
├── base/
│   ├── kustomization.yaml             # base resource list
│   ├── namespace.yaml                 # verdictcouncil namespace (overlays patch this)
│   ├── deployment-api-service.yaml    # uvicorn :8001 (FastAPI)
│   ├── deployment-arq-worker.yaml     # arq Redis-backed worker
│   ├── service-api-service.yaml       # ClusterIP for the API
│   ├── ingress.yaml                   # NGINX Ingress, TLS via cert-manager
│   ├── cronjob-stuck-case-watchdog.yaml
│   ├── job-alembic-migrate.yaml       # one-shot migration, applied per deploy
│   └── secrets.yaml                   # placeholder; rendered live by the workflow
└── overlays/
    ├── staging/                       # patches namespace + ingress host
    └── production/
```

## Two-component shape, single image

The same Docker image (`Dockerfile`, multi-stage) deploys as two
Deployments that differ only in their `command`/`args`:

- **`api-service`** runs `uvicorn src.api.app:app --host 0.0.0.0 --port 8001`
  and is fronted by `service-api-service.yaml` + `ingress.yaml`.
- **`arq-worker`** runs `arq src.workers.worker_settings.WorkerSettings`,
  drains the `pipeline_jobs` outbox in Postgres, and runs the cron jobs
  declared in `WorkerSettings.cron_jobs` (e.g. domain reconciliation).

The watchdog that flips long-stuck `processing` cases to
`failed_retryable` runs as the `stuck-case-watchdog` CronJob (every
5 min, 30-min stale threshold).

## What's deliberately not in kustomize

`secrets.yaml` and `job-alembic-migrate.yaml` are **not** in
`base/kustomization.yaml` because the deploy workflow renders them at
apply time:

- The Secret is rebuilt from GitHub Actions secrets via
  `kubectl create secret … --dry-run=client -o yaml | kubectl apply -f -`.
- The migration Job is patched with the freshly-built image tag and
  applied separately so it runs once per deploy.

## Why DOKS, not App Platform

Earlier drafts considered DigitalOcean App Platform for backend deploys.
We're on DOKS because the assessment rubric rewards a Kubernetes
deployment, and DOKS gives us native control over the per-component
shape (HPA, NetworkPolicy, sidecars later) without re-platforming.
The frontend remains on App Platform — see
`../../VerdictCouncil_Frontend/.do/app.production.yaml`.

## Provisioning prerequisites

Before the first deploy can succeed:

1. DOKS cluster (sgp1) created via `doctl kubernetes cluster create`.
2. DO Container Registry created and bound to the cluster:
   `doctl kubernetes cluster registry add <cluster-id>`.
3. DO Managed Postgres + Managed Redis in the same VPC; connection
   strings live in GitHub Action secrets and get rendered into the
   `verdictcouncil-secrets` Secret on every deploy.
4. NGINX Ingress controller + cert-manager installed in the cluster
   (the Ingress manifest references `nginx` ingressClassName and
   `letsencrypt-prod` ClusterIssuer).
5. DNS records for `staging-api.verdictcouncil.sg` and
   `api.verdictcouncil.sg` pointing at the LoadBalancer the Ingress
   provisions.

See `docs/architecture/08-infrastructure-setup.md` for the full
provisioning runbook.
