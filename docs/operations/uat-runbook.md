# UAT Runbook — VerdictCouncil Staging

This runbook walks QA through exercising the full SAM mesh pipeline against
the `verdictcouncil-staging` DOKS cluster. It assumes `kubectl` is
configured against the staging cluster and `staging-api.verdictcouncil.sg`
resolves to the ingress.

## Smoke test: cluster health

```bash
NS=verdictcouncil-staging
kubectl get pods -n $NS
```

Expected state:
- `solace-broker-0/1/2` — all Running.
- `solace-bootstrap-*` — Completed (1/1).
- 9 agent deployments + `layer2-aggregator` + `api-service` + `web-gateway` — all Ready.

If any pod is CrashLoopBackOff, check `docs/operations/solace-ha-runbook.md`
first — most failure modes trace back to auth or VPN bootstrap.

## Exercise the mesh pipeline end-to-end

1. Authenticate as a judge role:
   ```bash
   TOKEN=$(curl -sf -X POST https://staging-api.verdictcouncil.sg/api/v1/auth/login \
     -H 'Content-Type: application/json' \
     -d '{"email":"uat-judge@example.org","password":"<uat-password>"}' | jq -r .access_token)
   ```

2. Create a case:
   ```bash
   CASE_ID=$(curl -sf -X POST https://staging-api.verdictcouncil.sg/api/v1/cases \
     -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' \
     -d @tests/fixtures/sample_case.json | jq -r .id)
   ```

3. Trigger mesh execution (this is the POST endpoint introduced in
   Phase 2 — all 9 SAM agents run distributed over Solace):
   ```bash
   curl -sf -X POST https://staging-api.verdictcouncil.sg/api/v1/cases/$CASE_ID/process \
     -H "Authorization: Bearer $TOKEN"
   ```

4. Subscribe to the progress SSE stream in a second terminal:
   ```bash
   curl -N -H "Authorization: Bearer $TOKEN" \
     https://staging-api.verdictcouncil.sg/api/v1/cases/$CASE_ID/status/stream
   ```
   Expect a sequence of `PipelineProgressEvent` JSON messages, one per agent,
   ending with `deliberation` → `governance-verdict` → `pipeline_complete`.
   The 3-way fan-in (evidence/fact/witness → legal-knowledge) should fire
   nearly simultaneously, then wait on the aggregator barrier.

5. Verify the final verdict persisted:
   ```bash
   curl -sf https://staging-api.verdictcouncil.sg/api/v1/cases/$CASE_ID \
     -H "Authorization: Bearer $TOKEN" | jq '.verdict_recommendation'
   ```

Pass criteria:
- SSE stream delivers events for all 9 agents without gaps.
- `pipeline_complete` arrives within the UAT SLA (currently 90s for the
  sample case; raise a bug if slower than 180s without explanation).
- The final case row has `status = decided`, `verdict_recommendation` populated,
  and at least one row in `pipeline_checkpoints` per agent step (Phase 2
  mid-pipeline persistence — check via the case detail endpoint's audit fields).

## Failover during a live run

This validates that the 3-node HA survives a broker outage mid-pipeline.

1. Start a fresh pipeline (`POST /cases/$ID/process`).
2. In a second terminal, identify the active Solace pod:
   ```bash
   kubectl get pod -n $NS -l active=true -o name
   ```
3. Kill it before the 3-way fan-in finishes:
   ```bash
   kubectl delete pod -n $NS solace-broker-0 --grace-period=15
   ```
4. Watch the SSE stream from step 4 above.

Pass criteria:
- SSE stream pauses briefly (10–30s) then resumes.
- Pipeline eventually reports `pipeline_complete`.
- No manual intervention required — the mesh runner's `await_response` wrapper retries.

## What-If mode

The Contestable Judgment Mode still uses the in-process runner (see the
`revert(what-if)` commit on this branch) — what-if scenarios do not
exercise the mesh path. UAT coverage for what-if is the existing
`POST /cases/{id}/what-if` + `GET` poll flow. Document any mesh-migration
attempt for what-if as out of scope for this UAT cycle.

## Rollback

If a UAT failure requires reverting to the previous release:

```bash
# Find the previous rc tag
git tag --sort=-v:refname | grep "v0\." | head -5
# Re-deploy the prior rc (example)
git checkout v0.3.0-rc.1
./scripts/deploy-staging.sh   # or re-run the GH Action on the prior SHA
```

Do NOT modify the staging Solace StatefulSet directly — roll back the
committed manifest instead so the rendered-chart provenance stays intact.

## Known deviations from production parity

- Admin password is still pinned to `admin` in the chart values; production
  must switch to `usernameAdminPasswordSecretName` before cutover.
- The SEMPv2 bootstrap uses `publishTopicDefaultAction: allow` / `subscribeTopicDefaultAction: allow` for speed. Production bootstrap must tighten ACLs per the A2A topic convention (see `configs/services/layer2-aggregator.yaml:13-20`).
- No TLS yet on SMF — staging runs plaintext on `tcp://solace-broker:55555`.

## Escalation contacts

- On-call rotation: see `docs/operations/solace-ha-runbook.md` escalation section.
- UAT blockers: file in Linear under the `VER` project, tag `uat-blocker`.
