# Monitoring Stack

VerdictCouncil deploys `kube-prometheus-stack` on DOKS, providing Prometheus,
Grafana, Alertmanager, kube-state-metrics, and node-exporter.

## Install / upgrade

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n kube-prometheus-stack --create-namespace \
  -f k8s/monitoring/values.yaml \
  --set grafana.adminPassword="<your-secure-password>"
```

Then apply the ServiceMonitor so Prometheus scrapes the backend `/metrics` endpoint:

```bash
kubectl apply -f k8s/monitoring/servicemonitor-api.yaml
```

## Accessing UIs (port-forward)

```bash
# Grafana  →  http://localhost:3000  (admin / <password set above>)
kubectl port-forward -n kube-prometheus-stack svc/kube-prometheus-stack-grafana 3000:80

# Prometheus  →  http://localhost:9090
kubectl port-forward -n kube-prometheus-stack svc/kube-prometheus-stack-prometheus 9090:9090

# Alertmanager  →  http://localhost:9093
kubectl port-forward -n kube-prometheus-stack svc/kube-prometheus-stack-alertmanager 9093:9093
```

## What's monitored

| Source | How |
|--------|-----|
| Node CPU/memory/disk | node-exporter DaemonSet (auto) |
| Kubernetes objects | kube-state-metrics (auto) |
| Backend `/metrics` | `servicemonitor-api.yaml` → scrapes `service-api-service:8001/metrics` |

## Production / staging policy

The live staging and production clusters use the **DigitalOcean Marketplace
`kubernetes-monitoring-stack` 1-click app** and must continue to be managed
through the DO control panel — do NOT run `helm upgrade` against these clusters
without explicit sign-off.

`values.yaml` is kept for two purposes only:
1. **Documentation** — records the configuration intent so the install is
   auditable from git.
2. **Fresh bootstrap** — used when rebuilding a cluster from scratch
   (`infra-bootstrap.yml`), where the 1-click app is unavailable via
   automation.

The only manifest safe to `kubectl apply` against the live cluster is
`servicemonitor-api.yaml`, which adds backend `/metrics` scraping and does
not modify the existing Prometheus/Grafana/Alertmanager deployment.
