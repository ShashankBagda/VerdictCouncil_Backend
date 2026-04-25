# Sprint 4 4.A3.13 — manual gate-flow smoke runbook

End-to-end verification that the saver-driven runtime cutover (4.A3.5–7,
4.A3.9, 4.A3.14) actually works against a real Postgres + Redis stack
and a live OpenAI run. Two paths — pick one:

- **Option A — UI smoke** — drive a case through the React UI. Matches
  the original 4.A3.13 spec; needs a browser.
- **Option B — Headless API smoke** — drive the same flow with `curl`
  + `jq`. No browser needed; same coverage.

Both paths spend real OpenAI tokens (rough estimate: $5–20 per run, a
few minutes wall clock per gate). Both require LangSmith access to
verify trace details.

---

## Pre-flight (both options)

```bash
# 1. Stack up
cd /Users/douglasswm/Project/AAS/VER
./dev.sh
# Verify in another terminal:
docker ps --format '{{.Names}}'   # should include vc-postgres, vc-redis
curl -sf http://localhost:8000/api/v1/health || echo "backend not up"
```

**Sanity-check env vars are populated:**

```bash
grep -E "^(OPENAI_API_KEY|LANGSMITH_API_KEY|JWT_SECRET|DATABASE_URL|REDIS_URL)=" \
  VerdictCouncil_Backend/.env | sed 's/=.*/=<set>/'
# All five should print "<set>"
```

**Seed a judge user** (skip if already present):

```bash
cd VerdictCouncil_Backend
uv run python scripts/seed_judge.py \
  --email judge@verdictcouncil.local \
  --password 'devjudge123!'
# (or whatever your seed script is — check scripts/seed_*.py)
```

**LangSmith tab open:** https://smith.langchain.com — bookmark the
project page so you can verify traces per gate.

---

## What you're verifying

The 4.A3.13 acceptance criteria, in plain English:

1. **gate1 → gate2 advance** — judge clicks advance at gate1; pipeline
   resumes through research and pauses at gate2. The LangSmith trace
   shows the resumed run linked to the same `case_id` metadata as
   gate1. The case status flips to `awaiting_review_gate2` in
   Postgres.
2. **gate2 rerun a single subagent** — judge clicks rerun on gate2
   with `subagent="evidence"` + `notes="weight matrix is wrong"`. The
   trace shows **only `research_evidence`** re-running (not
   facts/witnesses/law). LangSmith should show a new prompt commit
   for the evidence subagent (4.C3a self-correction loop).
3. **Cancel mid-pipeline halts ≤1 super-step** — start a fresh case,
   wait until the pipeline is mid-research (between gate1 and
   gate2), POST `/cancel`. Within one inter-turn window (~5–15s),
   the SSE stream emits a terminal event and the case status moves
   off `processing`. Saver state shows `halt.reason == "cancelled"`.
4. **(New — 4.A3.14) gate4 send_back** — let a case run all the way
   to gate4. Click the "Send back to ▼ synthesis" dropdown (UI) or
   POST `action=send_back, to_phase=synthesis`. Pipeline rewinds to
   gate3 with `extra_instructions[synthesis] = notes`. LangSmith
   history shows the original gate4 checkpoints + the new fork.

---

## Option A — UI smoke

### Setup

1. Open http://localhost:5173 in a browser.
2. Log in as the seeded judge.
3. Click **New case**.
4. Domain: `small_claims`. Title: `Sprint 4 4.A3.13 smoke`.
5. Add at least 2 parties (one claimant, one respondent).
6. Upload at least one PDF (any short document — the smoke verifies
   pipeline plumbing, not legal accuracy).
7. Click **Submit / Process**.

### Walkthrough

| # | Action | Expected | Verify in |
|---|---|---|---|
| 1 | Wait for gate1 pause | Case detail shows `<GateReviewPanel gate=1>` mounted; SSE shows `InterruptEvent` with `gate=gate1` | Browser DevTools → Network → SSE stream |
| 2 | Click **Advance** on gate1 panel | UI shows loading; SSE resumes; case status flips to `processing` then `awaiting_review_gate2` | Browser + `psql vc_dev -c "SELECT id, status FROM cases ORDER BY created_at DESC LIMIT 1"` |
| 3 | At gate2, in the per-subagent rerun list: check **Evidence** only; type `weight matrix is wrong` in the notes box; click **Rerun** | Only the evidence subagent re-runs; gate2 panel re-mounts after re-pause | LangSmith trace: only `research_evidence` should have a new run; facts/witnesses/law have just the original run from gate1→gate2 |
| 4 | Click **Advance** on gate2 (reformed) → **Advance** on gate3 → wait for gate4 | Each transition takes ~30–90s of LLM time | LangSmith — 1 synthesis trace, 1 audit trace |
| 5 | At gate4, if `recommend_send_back` is present in the audit summary: open the **Send back to ▼ synthesis** dropdown, type `redo conclusion 2`, click | Case status flips to `awaiting_review_gate3`; gate3 panel re-mounts | DB: `case.status = awaiting_review_gate3`. LangSmith: history span has new fork checkpoints; old gate4 checkpoints still listed |
| 6 | Click **Advance** through gate3 → gate4, then **Approve** at gate4 | Case reaches END; `judicial_decision` JSONB populated | DB: `case.status = closed`; `SELECT judicial_decision FROM cases WHERE id = '<uuid>'` shows the AI engagements |

### Cancel test (separate fresh case)

1. Submit a new case (steps 1–7 above).
2. Wait for the SSE stream to start emitting research-phase events (you'll see agent names like `research_facts` ticking).
3. **Don't** wait for gate2 — while research is mid-flight, open another tab and:
   ```
   POST /api/v1/cases/<case_id>/cancel
   ```
   (Or click **Cancel** in the UI if a cancel button is wired.)
4. Within ~15s, the SSE stream should emit a terminal/cancelled event and stop.
5. Verify in DB: `SELECT status FROM cases WHERE id = '<uuid>'` should NOT be `processing` (typically `failed` or `closed` depending on the legacy run-end status logic).
6. Verify in LangSmith: the run shows the cancellation.

---

## Option B — headless API smoke

Same coverage, no browser. Replace `<HOST>` with `http://localhost:8000`.

### Login + grab cookie

```bash
curl -c /tmp/vc-cookie.txt -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"judge@verdictcouncil.local","password":"devjudge123!"}'
# httpOnly cookie 'vc_token' lands in /tmp/vc-cookie.txt
```

All subsequent calls use `-b /tmp/vc-cookie.txt`.

### Get a domain UUID

```bash
DOMAIN_ID=$(curl -sb /tmp/vc-cookie.txt http://localhost:8000/api/v1/domains \
  | jq -r '.[] | select(.code=="small_claims") | .id')
echo "DOMAIN_ID=$DOMAIN_ID"
```

### Create a case

```bash
CASE_ID=$(curl -sb /tmp/vc-cookie.txt -X POST http://localhost:8000/api/v1/cases \
  -H 'Content-Type: application/json' \
  -d "{
    \"domain_id\": \"$DOMAIN_ID\",
    \"title\": \"Sprint 4 4.A3.13 smoke\",
    \"description\": \"headless smoke\",
    \"parties\": [
      {\"name\": \"Alice Claimant\", \"role\": \"claimant\", \"contact_info\": null},
      {\"name\": \"Bob Respondent\", \"role\": \"respondent\", \"contact_info\": null}
    ],
    \"claim_amount\": 5000
  }" | jq -r '.id')
echo "CASE_ID=$CASE_ID"
```

### Upload a document

```bash
curl -sb /tmp/vc-cookie.txt -X POST \
  "http://localhost:8000/api/v1/cases/$CASE_ID/documents" \
  -F 'file=@tests/fixtures/sample.pdf'
# Substitute any small PDF you have.
```

### Trigger the pipeline

```bash
curl -sb /tmp/vc-cookie.txt -X POST \
  "http://localhost:8000/api/v1/cases/$CASE_ID/process"
```

### Tail the SSE stream

In a second terminal — leave running through the whole smoke:

```bash
curl -Nb /tmp/vc-cookie.txt \
  "http://localhost:8000/api/v1/cases/$CASE_ID/status/stream"
# You'll see InterruptEvent / PipelineProgressEvent / etc. line by line.
# Watch for `"phase":"awaiting_review_gate1"` or `event: interrupt`.
```

### Test 1 — gate1 → gate2 advance

When the SSE shows the gate1 pause:

```bash
curl -sb /tmp/vc-cookie.txt -X POST \
  "http://localhost:8000/api/v1/cases/$CASE_ID/respond" \
  -H 'Content-Type: application/json' \
  -d '{"action":"advance","notes":"smoke gate1 advance"}'
```

**Expect:** 202 with `{"message":"Advancing to gate2"}`. SSE resumes;
case status flips to `awaiting_review_gate2` after the research phase.

```bash
psql -h localhost -U vc_dev -d verdictcouncil \
  -c "SELECT status FROM cases WHERE id='$CASE_ID'"
```

### Test 2 — gate2 rerun evidence subagent only

When SSE shows gate2 paused:

```bash
curl -sb /tmp/vc-cookie.txt -X POST \
  "http://localhost:8000/api/v1/cases/$CASE_ID/respond" \
  -H 'Content-Type: application/json' \
  -d '{
    "action":"rerun",
    "phase":"research",
    "subagent":"evidence",
    "notes":"weight matrix is wrong"
  }'
```

**Expect:** 202 with `{"message":"Re-running gate2"}`. SSE shows
`research_evidence` re-running. Verify in LangSmith UI:

- Open the case's run (filter by `case_id` metadata).
- Confirm the **second** research run has only one subagent span:
  `research_evidence`. The other three (`research_facts`,
  `research_witnesses`, `research_law`) should NOT have re-run.

### Test 3 — gate4 send_back to synthesis

Drive through gate2 → gate3 → gate4 by repeatedly POSTing
`{"action":"advance"}` after each pause. When gate4 pauses:

```bash
curl -sb /tmp/vc-cookie.txt -X POST \
  "http://localhost:8000/api/v1/cases/$CASE_ID/respond" \
  -H 'Content-Type: application/json' \
  -d '{
    "action":"send_back",
    "to_phase":"synthesis",
    "notes":"redo conclusion 2 with stricter uncertainty"
  }'
```

**Expect:** 202 with `{"message":"Sent back to synthesis; paused at gate3"}`. Case status flips to `awaiting_review_gate3`. Verify in DB.

```bash
psql -h localhost -U vc_dev -d verdictcouncil \
  -c "SELECT status FROM cases WHERE id='$CASE_ID'"
# expect awaiting_review_gate3
```

Verify in LangSmith UI: the thread's history shows the original gate4
checkpoints **plus** new fork checkpoints (rewind extends history,
doesn't truncate it).

### Test 4 — cancellation halts ≤1 super-step (fresh case)

Submit a new case and start it (`/process`). Watch the SSE stream.
While the pipeline is in research (you'll see `research_facts` /
`research_evidence` etc events), in another terminal:

```bash
curl -sb /tmp/vc-cookie.txt -X POST \
  "http://localhost:8000/api/v1/cases/$CASE_ID/cancel"
```

**Expect:** 202 with `{"message":"Cancellation requested"}`. Within
~15s the SSE stream emits a terminal frame. Verify:

```bash
# DB — status moved off processing
psql -h localhost -U vc_dev -d verdictcouncil \
  -c "SELECT status FROM cases WHERE id='$CASE_ID'"
```

The saver-halt path also writes `halt.reason="cancelled"` into the
LangGraph thread state. To inspect (the saver's state isn't surfaced
through an HTTP endpoint, so this is a Python REPL check):

```bash
cd VerdictCouncil_Backend
uv run python -c "
import asyncio
from src.pipeline.graph.runner import GraphPipelineRunner
async def main():
    runner = GraphPipelineRunner()
    state = await runner._graph.aget_state({'configurable': {'thread_id': '$CASE_ID'}})
    print('halt:', state.values.get('halt'))
    print('next:', state.next)
asyncio.run(main())
"
```

---

## Pass/fail checklist

Tick each line — all four must pass for 4.A3.13 to be considered done:

- [ ] **gate1 advance → gate2** — case status, SSE, and LangSmith trace consistent
- [ ] **gate2 rerun evidence subagent only** — LangSmith shows only `research_evidence` re-ran
- [ ] **gate4 send_back to synthesis** — re-pauses at gate3, history extends with fork
- [ ] **cancel halts ≤1 super-step** — SSE terminates within ~15s, halt slot populated

---

## Cleanup

```bash
# Stop just the app processes (Postgres + Redis stay up):
# Ctrl+C in the dev.sh terminal

# Stop everything including infra:
cd /Users/douglasswm/Project/AAS/VER && ./stop.sh --infra

# Delete the smoke case if you want a clean DB:
psql -h localhost -U vc_dev -d verdictcouncil \
  -c "DELETE FROM cases WHERE id='$CASE_ID'"
```

---

## After running

Update `docs/operations/sprint4-manual-ops.md`:

- Move `4.A3.13` from the "Still deferred" section to "Cutover follow-ups shipped" once all four checks pass.
- Capture the LangSmith run URLs from the four tests so future audits have the trace links.
