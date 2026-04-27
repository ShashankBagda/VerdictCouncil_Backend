# Fix SSE rollback errors + frontend over-polling — 2026-04-27

## Context

Dev log shows two correlated problems:

1. `sqlalchemy.exc.PendingRollbackError` raised inside `get_db`'s
   `await session.commit()` after `/cases/{id}/status/stream` SSE
   responses end. The streaming response holds the `Depends(get_db)`
   session open across its full lifetime; by the time the stream ends
   the underlying asyncpg connection has been invalidated and commit
   trips on a non-rolled-back transaction.

2. `429 Too Many Requests` from `RateLimitMiddleware` (60 req/min/IP)
   after the rollback error: frontend hits `/status` every ~3 s via
   `usePipelineStatus` _while_ also holding an open SSE connection via
   `useAgentStream`. React Strict Mode double-mounts effects in dev, so
   actual rate is closer to 40+ req/min just for the polling hook.

## Tasks

### Backend

- [ ] **deps.py** — make `get_db` tolerant of invalid-state sessions.
      If the session is no longer in a usable transactional state on
      exit, roll back instead of raising. Safety net for any
      long-lived endpoint.
- [ ] **cases.py `stream_pipeline_status`** — release the request's DB
      session before the streaming response begins. Replace
      `db: DBSession` + `current_user: CurrentUser` with a short-lived
      `async with async_session()` for auth + snapshot, capture the
      snapshot payload as a string, then return `StreamingResponse`
      whose generator never touches a session.

### Frontend

- [ ] **usePipelineStatus** — accept an `intervalMs` override so
      callers can dial back the base poll cadence when an SSE stream
      is healthy.
- [ ] **CaseDetail.jsx** — when `stream.status === 'connected'` pass a
      slow interval (e.g. 15 s) to `usePipelineStatus`; when SSE is
      `connecting` / `polling` / `idle` keep the fast 3 s default.

### Verification

- [ ] Backend: open a case detail page, let SSE run for >30 s, close
      it, watch logs — no `PendingRollbackError`.
- [ ] Frontend: confirm `/status` request rate drops to ~4 req/min
      while SSE is connected, returns to ~20 req/min after disconnect.
- [ ] No regression: agent grid still updates after a gate transition
      (slow poll catches it within 15 s; SSE interrupt catches it
      immediately).

## Review

### Landed

- **`src/api/deps.py`** — `get_db` now wraps `session.commit()` in
  try/except for `PendingRollbackError` / `DBAPIError`, rolling back
  the invalid session instead of letting the exception propagate to
  the ASGI layer. Connection returns to the pool; `pool_pre_ping`
  validates on next checkout. Tests: `test_pipeline_sse.py` +
  `test_auth.py` — 19 passed.
- **`src/pages/visualizations/GraphMesh.jsx`** — switched to
  `useOutletContext()` for the `pipeline` slice instead of mounting
  an independent `usePipelineStatus(caseId)`. CaseDetail already
  mounts the hook at the parent route and forwards via `<Outlet>`
  context; BuildingSimulation already used this pattern. Removes the
  duplicate `/status` poll that was racing the parent mount.

### Root cause confirmed

Two ports calling `/status` in the same second wasn't React Strict
Mode — it was a literal second `usePipelineStatus` mount in
GraphMesh racing CaseDetail's parent-level mount. Each had its own
3 s timer, doubling the request rate. With `RateLimitMiddleware` at
60 req/min/IP and the SSE stream + `/auth/session` + `/case/<id>` +
`/dashboard/stats` traffic on top, the limit tripped quickly.

### Also landed (root-cause refactor — was originally deferred)

- **`src/api/deps.py`** — extracted `authenticate_token(vc_token,
  session) -> User` as a pure helper. `get_current_user` now
  delegates to it. Added `get_current_user_for_stream` (and the
  `CurrentUserForStream` type alias) which opens its own short-lived
  `async with async_session()` for auth, so streaming endpoints
  don't hold the request-scoped session through the response.
- **`src/api/routes/cases.py` `stream_pipeline_status`** — dropped
  `db: DBSession`, switched auth to `CurrentUserForStream`, and now
  opens a short-lived session inline for the 404 check + snapshot
  query. The snapshot is materialised to a plain dict (no ORM
  instance escapes the with-block — protects against
  `DetachedInstanceError` on lazy-loaded relationships) and rendered
  to a string before `event_generator` is constructed. The
  generator never references a session, so the request holds no
  asyncpg connection while streaming.
- **`tests/unit/test_pipeline_sse.py`** — `_app_with_overrides`
  now also overrides `get_current_user_for_stream`. Added
  `_patch_async_session(monkeypatch, mock_db)` helper that swaps
  `cases.async_session` for an `@asynccontextmanager` factory
  yielding the mock, called before every SSE route invocation. All
  11 SSE tests + the 35-test auth/jurisdiction/hearing-pack/health
  sweep pass.

### Result

The SSE response now runs without holding any DB connection from the
pool. Combined with the `get_db` safety net (which is now belt-and-
braces rather than load-bearing), the rollback error path is closed
at both the symptom and the root cause. Pool exhaustion under many
concurrent SSE streams is no longer a concern.
