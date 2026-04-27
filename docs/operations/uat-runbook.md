# UAT Runbook — VerdictCouncil Staging

This runbook walks QA through exercising the in-process LangGraph
pipeline against the `verdictcouncil-staging` DOKS cluster. It assumes
`kubectl` is configured against the staging cluster and
`staging-api.verdictcouncil.sg` resolves to the Ingress.

> **Topology note.** Earlier drafts of this runbook described a Solace
> Agent Mesh (SAM) deployment with a 3-node broker, a `layer2-aggregator`
> service, and 9 per-agent containers. That design was decommissioned —
> the live deployment is a single image running as two K8s Deployments
> (`api-service` and `arq-worker`), with all agents executing in-process
> inside a LangGraph `StateGraph`. See `docs/architecture/02-system-architecture.md`.

## Smoke test: cluster health

```bash
NS=verdictcouncil-staging
kubectl get pods -n $NS
```

Expected state:
- `api-service-*` — Running, 1/1 Ready.
- `arq-worker-*` — Running, 1/1 Ready.
- `stuck-case-watchdog-*` — Completed (during the last 5-min cron firing) or absent.
- nginx-ingress controller pods — Running.

If any pod is `CrashLoopBackOff`, `kubectl logs` it and check the env vars
on the rendered `verdictcouncil-secrets` Secret first — most failure modes
trace back to a missing `DATABASE_URL` / `REDIS_URL` / `OPENAI_API_KEY`.

## Exercise the pipeline end-to-end

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

3. Trigger pipeline execution. The API enqueues the run via the Postgres
   outbox (`pipeline_jobs`); the `arq-worker` Deployment drains it and
   runs the LangGraph graph in-process:
   ```bash
   curl -sf -X POST https://staging-api.verdictcouncil.sg/api/v1/cases/$CASE_ID/process \
     -H "Authorization: Bearer $TOKEN"
   ```

4. Subscribe to the progress SSE stream in a second terminal:
   ```bash
   curl -N -H "Authorization: Bearer $TOKEN" \
     https://staging-api.verdictcouncil.sg/api/v1/cases/$CASE_ID/status/stream
   ```
   Expect a sequence of `PipelineProgressEvent` JSON messages spanning the
   intake → research fan-out (evidence + facts + witnesses + law) →
   research join → synthesis → audit phases, with a `pipeline_complete`
   terminator. Each Gate (1–4) emits a `gate_pause` event when the graph
   stops at `interrupt(...)` waiting for a reviewer decision.

5. Verify the final verdict persisted:
   ```bash
   curl -sf https://staging-api.verdictcouncil.sg/api/v1/cases/$CASE_ID \
     -H "Authorization: Bearer $TOKEN" | jq '.verdict_recommendation'
   ```

Pass criteria:
- SSE stream delivers events for all phases (intake → 4 research subagents → synthesis → audit) without gaps.
- `pipeline_complete` arrives within the UAT SLA (currently 90 s for the
  sample case; raise a bug if slower than 180 s without explanation).
- The final case row has `status = decided`, `verdict_recommendation` populated,
  and a complete `audit_log` (one entry per agent node + gate decision).
- LangSmith trace for the run shows the full graph execution
  (`thread_id` = case `run_id`).

## HITL gate flow

The pipeline pauses at four review gates. Each pause is a LangGraph
`interrupt(...)` and is observable as a `gate_pause` SSE event. To resume:

```bash
# After observing gate_pause for, e.g., gate2:
curl -sf -X POST "https://staging-api.verdictcouncil.sg/api/v1/cases/$CASE_ID/gates/gate2/resume" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"decision":"advance"}'
```

`decision` ∈ `{advance, rerun, halt}`. The graph picks up from the
checkpoint stored in `langgraph_checkpoint` (Postgres) and continues.

## Worker resilience

This validates that the pipeline survives an `arq-worker` pod restart
mid-run (which can happen during a deploy, an OOM kill, or a node drain).

1. Start a fresh pipeline (`POST /cases/$ID/process`).
2. After the SSE stream shows the run has progressed past `intake`, kill
   the worker pod:
   ```bash
   kubectl delete pod -n $NS -l component=arq-worker
   ```
3. Watch the SSE stream.

Pass criteria:
- The `arq-worker` Deployment recreates the pod within ~30 s.
- The pipeline either resumes from the last checkpoint (graceful) or the
  `stuck-case-watchdog` flips the case to `failed_retryable` within the
  30-min threshold (worst case).
- No manual intervention required.

## What-If mode

Contestable Judgment Mode runs through the same in-process LangGraph
runner. UAT coverage for what-if is the `POST /cases/{id}/what-if` +
`GET` poll flow. Validate that a what-if scenario produces a
`thread_id`-scoped sub-run distinct from the original case run.

## Rollback

If a UAT failure requires reverting to the previous release:

```bash
# Find the previous tag
git tag --sort=-v:refname | grep "v0\." | head -5

# Roll back via kubectl
kubectl set image -n $NS deployment/api-service api-service=${PREV_IMAGE}
kubectl set image -n $NS deployment/arq-worker  arq-worker=${PREV_IMAGE}
kubectl rollout status -n $NS deployment/api-service --timeout=300s
kubectl rollout status -n $NS deployment/arq-worker  --timeout=300s
```

Do NOT modify the staging deploy manifests directly — roll back via image
tag so the GitHub Actions audit trail stays consistent.

## Escalation contacts

- UAT blockers: file in Linear under the `VER` project, tag `uat-blocker`.
- Post-deploy regression: page the on-call via the team rotation channel
  and link the failing pipeline `thread_id` from LangSmith.
