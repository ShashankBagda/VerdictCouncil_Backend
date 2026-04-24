# Solace HA Runbook

Operations guide for the 3-node Solace PubSub+ HA redundancy group running in
DigitalOcean Kubernetes (`verdictcouncil-staging` and future production
namespaces).

## Topology

| Pod ordinal | Hostname | Role | Serves traffic? |
| --- | --- | --- | --- |
| 0 | `solace-broker-0` | primary | when `active="true"` |
| 1 | `solace-broker-1` | backup | when `active="true"` (after failover) |
| 2 | `solace-broker-2` | monitor | never (arbiter only) |

The `solace-broker` ClusterIP Service selects on `active: "true"` — only the
pod currently holding the active role receives client SMF (55555) and SEMP
(8080) traffic. A `podtagupdater` sidecar flips the label when failover
happens, so backend clients keep connecting to the same
`solace-broker:55555` DNS name with no reconnect logic changes.

Per-pod DNS addresses via the headless `solace-broker-discovery` Service:
`solace-broker-0.solace-broker-discovery.<ns>.svc`, and so on. Mate-link
(port 8741) and config-sync (8300/8301/8302) use these hostnames.

## Daily checks

- `kubectl get pods -n verdictcouncil-staging -l app.kubernetes.io/name=pubsubplus-ha` — all 3 should be `Running`, Ready `1/1`.
- `kubectl get svc -n verdictcouncil-staging solace-broker -o jsonpath='{.spec.selector}'` — must include `active:"true"`.
- SEMP health-check: `curl -u admin:<pw> http://<broker>:8080/SEMP/v2/monitor/about` should return 200.
- `kubectl get job solace-bootstrap -n verdictcouncil-staging` — `COMPLETIONS 1/1`. If it's failing, dump logs with `kubectl logs job/solace-bootstrap -n verdictcouncil-staging`; re-run via `kubectl delete job solace-bootstrap && kubectl apply -k k8s/overlays/staging`.

## Manual failover test

Use this before every major staging deploy and once a month in production.

```bash
NS=verdictcouncil-staging
# Identify the active pod
kubectl get pod -n $NS -l app.kubernetes.io/name=pubsubplus-ha,active=true -o name
# Kill it
kubectl delete pod -n $NS solace-broker-0 --grace-period=30
# Watch the backup take over
watch "kubectl get pod -n $NS -l active=true"
```

Expected timeline:
- t+0s: primary pod terminates.
- t+5–15s: backup's `active` label flips to `"true"`; Service endpoints update.
- t+15–40s: backend clients' existing SMF sessions reconnect (handled by the Solace SDK reconnect strategy in `src/pipeline/_solace_a2a_client.py`).
- t+60s: old primary rejoins as backup with state synced.

Pass criteria: `kubectl get pods -l active=true` returns exactly one pod at all times after the initial flip; Layer 2 aggregator barrier Redis entries don't go stale during the event (check `TTL` on `vc:mesh:barrier:*` keys).

## Common incidents

### All 3 pods report `No Active Broker`

Redundancy group hasn't formed — most common on first install if the
`pre-install` monitor PVC (`data-solace-broker-2`) wasn't created before the
StatefulSet pods tried to start.

Fix: `kubectl get pvc -n <ns> data-solace-broker-2`. If missing, `kubectl
apply -k k8s/overlays/<env>` again — the PVC is declared separately from
the StatefulSet volumeClaimTemplate for exactly this reason.

### Bootstrap Job reports `403 Forbidden` from SEMP

The generated admin password doesn't match the one the Job pulls from
`solace-broker-secrets`. This happens when `values.solace.usernameAdminPassword`
was left blank on first render (the chart auto-generates and stores it) but
a later render pinned a fixed password.

Fix: decode the live Secret and use that value:
```bash
kubectl get secret solace-broker-secrets -n <ns> \
  -o jsonpath='{.data.username_admin_password}' | base64 -d
```
Update the render values if needed and re-render.

### Client AUTH_FAIL after clean deploy

The SEMPv2 bootstrap Job didn't run (or ran but the VPN/user creation
failed silently). Re-check with:
```bash
kubectl exec -n <ns> solace-broker-0 -- \
  curl -s -u admin:<pw> http://localhost:8080/SEMP/v2/config/msgVpns/verdictcouncil
```
A `404` means the VPN isn't there — re-run the Job.

### Split-brain after mate-link partition

The monitor node is the tiebreaker; with it healthy, split-brain cannot
happen. If the monitor pod is down AND the primary/backup network
partitions, both sides go into standby and stop serving traffic (fail-safe,
not fail-available).

Fix: get the monitor pod back. If the monitor's PVC is corrupted, drop and
recreate — monitor state is ephemeral.

## Version / image pinning

Chart: `solacecharts/pubsubplus-ha` 3.9.0.
Image: `solace/solace-pubsub-standard:10.25.0.217`.

Both are pinned in `k8s/base/solace-ha.values.yaml`. Re-render the manifest
after changes; see the `DO NOT EDIT BY HAND` header in `k8s/base/solace-ha.yaml`.

## Escalation

- Solace Community: https://solace.community
- SEMPv2 API reference: https://docs.solace.com/API-Developer-Online-Ref-Documentation/swagger-ui/config/index.html
