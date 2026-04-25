# Sprint 4 — pending manual operations

Authored 2026-04-25. This is the consolidated checklist of human-only
items left after the Sprint 4 backend cutover (`841ae4c`). None of
these can be done by an agent in this repo — they require the GitHub
UI, the LangSmith console, or hands-on smoke testing.

Cross-references:

- Eval-gate setup: [`docs/setup-2026-04-25.md`](../setup-2026-04-25.md)
- Worker-rewrite deferral: `tasks/sprint4-deferral-2026-04-25.md` (root repo)
- Sprint breakdown: `tasks/tasks-breakdown-2026-04-25-pipeline-rag-observability.md` (root repo)

---

## Eval gate (4.D3.2 / 4.D3.3 / 4.D3.4)

The `.github/workflows/eval.yml` workflow is committed but inert until
the operator wires it up.

### 1. Repo secrets (Settings → Secrets and variables → Actions → Secrets)

| Secret | Source |
|---|---|
| `LANGSMITH_API_KEY` | smith.langchain.com → Settings → API Keys |
| `OPENAI_API_KEY` | platform.openai.com → API Keys (only required when running `--mode graph` locally; CI uses `--mode stub`) |

The legacy `MLFLOW_TRACKING_URI` and `COHERE_API_KEY` secrets are not
required and can be deleted if present.

### 2. Repo variable (Settings → Secrets and variables → Actions → Variables)

| Variable | Value |
|---|---|
| `EVAL_BASELINE_EXPERIMENT` | LangSmith experiment name from Sprint 3 3.D1.4 baseline (e.g. `baseline-<sha>-stub`) |

If unset, the workflow runs in observe-only mode (warning emitted, no
gate fires). Bumping the baseline after a sanctioned model change is
a one-line variable update.

### 3. Branch protection (Settings → Branches → Branch protection rules)

For `main`, `release/*`, `development`:

- ☑ Require a pull request before merging
- ☑ Require status checks to pass before merging
  - `Lint`
  - `Unit Tests`
  - `Security`
  - **`Eval Gate`** ← from `.github/workflows/eval.yml`
  - `Docker Build` (if applicable)
- ☑ Require conversation resolution before merging

CODEOWNERS intentionally not used (per 2026-04-25 user decision).

### 4. End-to-end smoke (4.D3.4)

Validate both the failure path and the bypass path:

1. Open a PR that touches `prompts.py` and deliberately mis-aligns a
   prompt the goldens depend on.
2. Confirm the workflow runs and the comparison step fails red.
3. Apply the `eval/skip-regression` label and re-run the workflow.
4. Confirm the comparison step short-circuits and the gate passes.
5. Drop the label; close the PR without merging.
6. Capture the two GitHub Actions run URLs in this runbook.

---

## Frontend integrations gated by manual setup

These are blocked on operator-supplied credentials; the agent code
will land in a follow-up frontend sprint but cannot run without these.

### Sentry → LangSmith trace tagging (4.C5.1)

| Variable | Where | Source |
|---|---|---|
| `VITE_SENTRY_DSN` | `VerdictCouncil_Frontend/.env` | sentry.io → Project Settings → Client Keys (DSN) |

The Sentry project must be created beforehand. The frontend will tag
each event with `backend_trace_id` and a deep-link to the LangSmith
trace; both URLs are read from the SSE stream's `trace_id` field
(already shipped in Sprint 2 2.C1.5).

### LangGraph Cloud (Sprint 5 deployment)

When the time comes, Sprint 5 deployment requires LangGraph Cloud
project provisioning + a deploy key. Defer until Sprint 4 frontend
work lands.

---

## Worker-side runtime cutover (deferred from this branch)

The backend `841ae4c` ships the Sprint 4 A3 contract layer (gate
pause payload, `InterruptEvent`, `publish_interrupt()`, unified
`POST /cases/{id}/respond` endpoint, `ResumePayload`) but **the
worker still routes via the legacy `runner.run_gate(...)` path**.
Specifically:

- `run_gate_job` in `src/workers/tasks.py` does not consume the new
  `resume_action` / `phase` / `subagent` / `field_corrections` keys
  the `/respond` endpoint enqueues.
- `publish_interrupt()` is defined and tested but never called from
  any production path — the legacy `PipelineProgressEvent(phase=
  "awaiting_review")` is still what the worker emits at gate pause.
- The graph never receives `Command(resume=...)` end-to-end through
  the API → worker → graph round-trip.

Tasks parked behind this gap:

| Task | Description |
|---|---|
| 4.A3.5 | `/advance` becomes thin wrapper over `/respond` |
| 4.A3.6 | `/rerun` phase-level (BE side; FE rerun UI also needs update) |
| 4.A3.9 | Cancellation via `graph.update_state(halt=...)` instead of Redis flag |
| 4.A3.10–12 | Three integration tests that exercise the full round-trip |
| 4.A3.13 | Manual gate-flow smoke (depends on 4.A3.10–12) |
| 4.A3.14 | Auditor `send_back` mechanic (`/respond` returns 501 today) |

Suggested follow-up branch name: `feat/sprint4-a3-runtime-cutover`.

---

## Frontend gate panels (4.C5b.1–5)

The TS-side `ResumePayload` type must mirror the Python schema in
`src/api/schemas/resume.py` exactly. Components to build:

- `<GateReviewPanel>` shared component (4.C5b.1)
- `<Gate1IntakeReview>` / `<Gate2ResearchReview>` /
  `<Gate3SynthesisReview>` / `<Gate4AuditorReview>` (4.C5b.2)
- SSE consumer mounts panel on `InterruptEvent` (4.C5b.3) — note
  this depends on the worker-rewrite branch above to actually emit
  `InterruptEvent` in production
- Vitest unit tests per panel (4.C5b.4)
- End-to-end manual smoke (4.C5b.5)

---

## What-If LangGraph fork (4.A5)

Backend-only refactor; replaces the legacy `WhatIfController`
deep-clone with `update_state(past_config, Overwrite)` +
`invoke(None, fork_config)`. Independent of the worker rewrite —
can land in parallel.
