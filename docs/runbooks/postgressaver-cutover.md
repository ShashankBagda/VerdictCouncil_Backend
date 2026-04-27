# AsyncPostgresSaver cutover runbook

**Sprint 2 / task 2.A2.9** — procedure for switching the production
pipeline state-of-record from the bespoke `pipeline_checkpoints` table
to LangGraph's `AsyncPostgresSaver`.

The saver has been wired at compile time since Sprint 1 (`1.A1.PG`,
backend commit `834ae9d`) and the worker has read prior gate state via
`graph.aget_state(...)` since Sprint 2 (`2.A2.6`, commit `e6c748e`).
This runbook performs the cutover itself: drain the queue, copy
in-flight state into the saver, deploy, verify, resume intake.

## Pre-flight

Confirm the following before scheduling the maintenance window:

- [ ] Sprint 2 chain through 2.A2.8 is on `main` (CI green).
- [ ] `python scripts/check_casestate_serialization.py` exits 0
      (artefact: `tasks/serialization-audit-<date>.md`).
- [ ] `pytest tests/integration/test_checkpointer.py` is green on the
      release candidate.
- [ ] `pytest tests/integration/test_worker_gate_run.py` is green.
- [ ] Production Postgres has a fresh full backup taken within the
      last 24 hours. Note its identifier — it is the rollback target.
- [ ] Release tag `pre-postgres-saver-cutover` has been pushed
      (2.A2.10 — see "Tag" below).

The maintenance window should be sized for **30 min planned + 30 min
buffer**. The migration script's wall time scales with in-flight case
count; budget ~1 s per case.

## Tag

From `main`, tag the pre-cutover commit:

```bash
git checkout main
git pull --ff-only origin main
git tag -a pre-postgres-saver-cutover -m "Sprint 2 A2 — pre-cutover snapshot"
git push origin pre-postgres-saver-cutover
```

This tag is the rollback target for the application layer (the DB
backup is the rollback target for state).

## Cutover

### 1. Pause intake

Stop accepting new cases so in-flight work has a fixed boundary.

The system-level `pause_intake` flag (Sprint 4 wire-up) is not yet
read at intake; operate at the deployment layer instead:

- **Kubernetes**: scale the API deployment's intake routes via the
  feature-gate env var, or take the intake ingress out of rotation:
  ```bash
  kubectl patch deployment verdictcouncil-api \
    -p '{"spec":{"template":{"metadata":{"annotations":{"intake_paused":"1"}}}}}'
  ```
  In current revisions intake is best paused by removing the
  `/api/v1/cases POST` route from the ingress allow-list, or by
  scaling the deployment to 0 if a brief outage is acceptable.
- **Docker Compose / dev**: stop the API container; arq workers
  continue draining the queue.

Verify no new `pipeline_jobs` rows are landing:

```sql
SELECT count(*) FROM pipeline_jobs
WHERE status = 'pending' AND created_at > NOW() - interval '5 minutes';
```

### 2. Drain workers

Wait for all `pipeline_jobs` rows in `dispatched` to flip to
`completed` or `failed`. The dispatcher's stuck-job recovery loop
handles abandoned rows after 20 minutes; do not advance until the
queue is genuinely empty:

```sql
SELECT status, count(*) FROM pipeline_jobs
WHERE status IN ('pending','dispatched') GROUP BY status;
```

Both rows should return zero. If `dispatched` rows remain past 20
minutes, investigate before continuing — a stuck row indicates a
worker crash whose state may not migrate cleanly.

### 3. Run the migrator (dry-run, then real)

Dry-run reports the plan without writing:

```bash
cd VerdictCouncil_Backend
uv run python scripts/migrate_in_flight_cases.py --dry-run
```

Inspect the output:

- `rows scanned`: total `pipeline_checkpoints` rows for non-terminal
  cases.
- `to migrate`: thread count to be seeded into the saver. Ideally
  matches the number of distinct in-flight cases.
- The per-thread list shows `thread_id ← latest run_id @ agent /
  updated_at` — sanity-check that the chosen run_ids are recent.

Then execute:

```bash
uv run python scripts/migrate_in_flight_cases.py
```

The migrator is idempotent — re-running it skips threads the saver
already knows about. If it fails partway, re-run.

### 4. Deploy the new build

Promote the release artefact (whatever process you use for prod
deploys). The release must include backend commit `74efd25` or later
(Sprint 2 2.A2.8) so all four pillars are present:
- Saver wired at compile time (1.A1.PG)
- `gate_run` reads via `graph.aget_state` (2.A2.6)
- Migrator script (2.A2.7)
- Saver-API integration tests (2.A2.8)

Confirm the new version is live before continuing:

```bash
curl -fsS https://<api-host>/api/v1/health/ | jq .
```

### 5. Smoke verification

Submit a fresh test case (e.g., a small-claims fixture) via the API
and watch it pass at least gate 1:

```bash
# substitute your prod-safe smoke fixture
curl -X POST https://<api-host>/api/v1/cases \
  -H "Content-Type: application/json" \
  --cookie "vc_token=<judge-jwt>" \
  -d @tests/fixtures/smoke/case_minimal.json
```

Then for an existing in-flight case, advance one gate and confirm:

- LangSmith shows the run with `metadata.trace_id` set
  (Sprint 2 C1 work).
- The SSE stream receives `progress` events carrying `trace_id`.
- The `pipeline_checkpoints` table is **not** updated (the saver is
  the only writer now). Confirm with:
  ```sql
  SELECT max(updated_at) FROM pipeline_checkpoints;
  ```
  The timestamp should not advance after the cutover commit.

### 6. Resume intake

Reverse the step-1 changes: take the API back into rotation, scale
back up, or remove the env-var pause flag. Verify:

```sql
SELECT count(*) FROM pipeline_jobs
WHERE created_at > NOW() - interval '5 minutes';
```

The count should rise as new cases come in.

## Post-cutover

- File a follow-up ticket for **2.A2.11** — drop the
  `pipeline_checkpoints` table after one full release cycle of stable
  saver operation. Do not delete sooner: the legacy table is the
  fastest rollback path during the burn-in window.
- Watch error budgets and LangSmith for at least 24 hours before
  declaring success.
- Tag the post-cutover commit `post-postgres-saver-cutover` for ops
  bookkeeping.

## Rollback

Rollback is a two-step revert: application then state.

Trigger criteria — any one of these is sufficient:

- New cases hang at gate 1 (saver write failure).
- LangSmith / OTEL trace volume drops to zero post-deploy
  (instrumentation regression).
- `gate_run` errors with `No checkpointer set` or
  `connection refused` (saver lifespan not held).
- Data corruption signal: a case that was at `awaiting_review_gate2`
  resumes from gate 1.

### Rollback procedure

1. **Pause intake** again (step 1 of cutover).
2. **Revert the application** to the `pre-postgres-saver-cutover`
   tag:
   ```bash
   git checkout pre-postgres-saver-cutover
   # promote this commit through your normal deploy pipeline
   ```
   Verify the rolled-back build is live (`curl /api/v1/health/`,
   confirm version string or commit SHA).
3. **Restore Postgres** from the pre-window backup if any saver
   writes already landed in the new tables (`checkpoints`,
   `checkpoint_writes`, `checkpoint_blobs`, `checkpoint_migrations`).
   These tables are managed by `AsyncPostgresSaver.setup()` and do
   not need to exist for the rolled-back code path.

   If no saver writes occurred (e.g., rollback fired before step 5),
   you can skip the restore and just truncate the saver tables:
   ```sql
   TRUNCATE checkpoints, checkpoint_writes, checkpoint_blobs;
   ```
   The legacy `pipeline_checkpoints` rows are untouched by the
   migrator (it only reads), so the rolled-back code reads its old
   state-of-record cleanly.
4. **Resume intake**.
5. File an incident report. Capture: cutover start time, rollback
   trigger, recovery time, root cause if known, any cases observed in
   inconsistent state.

## Reference

- Sprint plan: `tasks/tasks-breakdown-2026-04-25-pipeline-rag-observability.md`
  workstream A2.
- Saver wiring: `src/pipeline/graph/checkpointer.py` (lifespan + module-level singleton).
- Worker entrypoint: `src/workers/tasks.py:run_gate_job` (reads via `graph.aget_state`).
- Migrator: `scripts/migrate_in_flight_cases.py`.
- Serialization audit: `tasks/serialization-audit-<date>.md`
  (regenerate via `scripts/check_casestate_serialization.py`).
- Saver-API tests: `tests/integration/test_checkpointer.py`.
