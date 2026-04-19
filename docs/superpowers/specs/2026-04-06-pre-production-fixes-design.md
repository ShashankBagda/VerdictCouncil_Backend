# Pre-Production Fixes Design Spec

**Date:** 2026-04-06
**Scope:** 3 pre-production fixes + TODOS.md update for deferred user stories
**Revision:** v3 — addresses Codex adversarial review findings

---

## Context

The VerdictCouncil backend has 3 pre-production issues identified in `TODOS.md` that need resolution before go-live. Additionally, 12 outstanding user stories (groups B, C, D) need to be logged in TODOS.md for future work.

---

## Fix 1: Cookie Secure Flag

**Problem:** `src/api/routes/auth.py:108` hardcodes `secure=True` on the `vc_token` JWT cookie. Browsers refuse to send cookies over `http://localhost`, breaking local dev auth.

**Solution:**

1. **`src/shared/config.py`** — Add setting:
   ```python
   cookie_secure: bool = True
   ```
   Defaults to `True` (production-safe). Pydantic `BaseSettings` parses `COOKIE_SECURE=false` from `.env` as `False` automatically. **Operational note:** local dev `.env` must include `COOKIE_SECURE=false`; production deployments must NOT set this variable (default `True` applies).

   Add a startup warning in `model_post_init` (mirrors the existing `jwt_secret` pattern):
   ```python
   if not self.cookie_secure and self.log_level != "DEBUG":
       warnings.warn(
           "COOKIE_SECURE is False. Session cookies will be sent over "
           "insecure HTTP. This must ONLY be used in local development.",
           stacklevel=2,
       )
   ```
   This prevents a copied dev `.env` from silently dropping all sessions to insecure transport in production.

2. **`src/api/routes/auth.py`** — Centralize cookie attributes. Both `login` (line 104-111) and `logout` (line 124) touch the `vc_token` cookie. Define cookie kwargs once and use them in both places:
   ```python
   _COOKIE_KWARGS = {
       "key": "vc_token",
       "httponly": True,
       "samesite": "lax",
   }
   ```
   - `login`: `response.set_cookie(**_COOKIE_KWARGS, value=token, secure=settings.cookie_secure, max_age=TOKEN_EXPIRY_HOURS * 3600)`
   - `logout`: `response.delete_cookie(**_COOKIE_KWARGS, secure=settings.cookie_secure)`

   This ensures the full cookie contract is in one place and `logout` mirrors `login` attributes.

**Files:** `src/shared/config.py`, `src/api/routes/auth.py`
**Migration:** None

---

## Fix 2: Total Precedent Source Failure Flag

**Problem:** When both PAIR API and the vector store return zero results, the pipeline treats it as "no relevant precedents found" rather than "all sources failed." The governance agent cannot distinguish between genuinely novel cases and total source failure, which affects fairness auditing.

**Design constraints:**
- `CaseState` already has `precedent_source_metadata: dict | None` (line 66 of `case_state.py`)
- Field ownership in `validation.py` already allows `legal-knowledge` to write `precedent_source_metadata`
- `search_precedents()` returns `list[dict]` and the SAM wrapper (`search_precedents_tool.py:89`) passes through the return value — the return type must stay `list[dict]`
- `vector_store_fallback.py` returns `[]` for both "no matches" and "API failure" — these must be distinguished
- Vector store returns `[]` when `openai_vector_store_id` is unconfigured — this is a configuration failure, not "no matches"

**Solution:**

1. **`src/tools/vector_store_fallback.py`** — Distinguish "no matches" from "API/config failure". Two changes:
   ```python
   class VectorStoreError(Exception):
       """Raised when vector store search encounters an unrecoverable error."""
   ```
   - **Config guard** (line 30-32): change from returning `[]` to raising `VectorStoreError`:
     ```python
     if not settings.openai_vector_store_id:
         logger.warning("OPENAI_VECTOR_STORE_ID not configured; cannot use vector store fallback")
         raise VectorStoreError("Vector store not configured")
     ```
   - **API failure** (line 72-74): re-raise after logging:
     ```python
     except Exception as exc:
         logger.warning("Vector store fallback failed: %s", exc)
         raise VectorStoreError(str(exc)) from exc
     ```
   Empty list return (`[]`) now strictly means "searched successfully, no matches."

2. **`src/tools/search_precedents.py`** — Replace module-level mutable state with a `SearchResult` dataclass to avoid async race conditions between concurrent pipeline runs:
   ```python
   from dataclasses import dataclass, field

   @dataclass
   class SearchResult:
       precedents: list[dict] = field(default_factory=list)
       metadata: dict[str, Any] = field(default_factory=lambda: {
           "source_failed": False,
           "fallback_used": False,
           "pair_status": "ok",
       })
   ```

   Refactor the internal implementation to build and return `SearchResult`, then split the public API:
   - `search_precedents()` — keeps existing signature, returns `list[dict]` (backward-compatible for SAM wrapper). Calls the internal function and returns only `result.precedents`.
   - `search_precedents_with_meta()` — new function, returns `SearchResult` (used by the pipeline runner). Calls the same internal function and returns the full object.

   This eliminates the module-level mutable state entirely. No race condition, no cross-request contamination.

   **Cache fix:** Include metadata in the cached payload. Instead of caching just the results list, cache the full `SearchResult` as JSON:
   ```python
   cached_payload = {"precedents": results, "metadata": metadata}
   await r.setex(cache_k, settings.precedent_cache_ttl_seconds, json.dumps(cached_payload))
   ```
   On cache hit, reconstruct both `precedents` and `metadata` from the cached JSON. This prevents a transient outage from caching an empty `[]` that replays as "no precedents found" for 24 hours.

   Set `source_failed = True` when:
   - PAIR API failed AND vector store raised `VectorStoreError` (total failure)
   - Circuit breaker OPEN AND vector store raised `VectorStoreError` (both unavailable)
   - Circuit breaker OPEN AND vector store returned `[]` (PAIR never tried, vector store found nothing — all sources exhausted)

   Set `source_failed = False` when:
   - PAIR succeeds (regardless of result count)
   - PAIR fails but vector store returned results (fallback worked)
   - PAIR fails but vector store returned `[]` without raising (searched successfully, legitimately no matches)

3. **`src/pipeline/runner.py`** — In `_execute_tool_call` (line 475-478), use `search_precedents_with_meta()` instead of `search_precedents()`:
   ```python
   elif tool_name == "search_precedents":
       from src.tools.search_precedents import search_precedents_with_meta
       search_result = await search_precedents_with_meta(**arguments)
       result = search_result.precedents
       # Store metadata for post-agent CaseState update
       self._pending_precedent_meta = search_result.metadata
   ```
   Store `_pending_precedent_meta` as an instance variable on `PipelineRunner` (not module-level — each runner instance handles one pipeline run, so no race).

   After the `legal-knowledge` agent completes in `_run_agent`, before field ownership validation: if `self._pending_precedent_meta` exists and `source_failed` is `True`, set `state.precedent_source_metadata = self._pending_precedent_meta`. Initialize `_pending_precedent_meta = None` in `__init__` and reset after each agent step.

4. **`src/tools/sam/search_precedents_tool.py`** — No changes needed. The SAM wrapper calls `search_precedents()` (not `search_precedents_with_meta()`), so the return type stays `list[dict]`. SAM-path metadata propagation is deferred — the SAM agents would need their own mechanism to write `precedent_source_metadata` via the event broker, which is out of scope for this fix. Logged as a known limitation.

5. **`configs/agents/governance-verdict.yaml`** — Add to the system prompt's guardrails section:
   > "Check `precedent_source_metadata` in the case state. If it contains `source_failed: true`, the fairness report MUST flag this as a limitation: the verdict was reached without precedent support due to source unavailability, not due to absence of relevant case law. This distinction affects the confidence score."

**Known limitation:** This fix only covers the `PipelineRunner` path. The SAM mesh path does not propagate `precedent_source_metadata` because the SAM wrapper returns `list[dict]` and the event broker does not carry tool-level metadata. This is acceptable for go-live since the `PipelineRunner` is the current runtime path. SAM metadata propagation should be addressed when SAM becomes the production runtime.

**Verification test cases (7 scenarios):**
- PAIR fails + vector store raises `VectorStoreError` → `source_failed: true`
- PAIR fails + vector store returns `[]` (no matches) → `source_failed: false`
- PAIR fails + vector store returns results → `source_failed: false`, `fallback_used: true`
- Circuit breaker OPEN + vector store raises `VectorStoreError` → `source_failed: true`
- Circuit breaker OPEN + vector store returns `[]` → `source_failed: true`
- Circuit breaker OPEN + vector store returns results → `source_failed: false`, `fallback_used: true`
- PAIR succeeds with `[]` → `source_failed: false`
- **Cache round-trip:** trigger outage (source_failed=true), verify cached payload includes metadata, verify cache hit returns correct metadata on next call

**Files:** `src/tools/vector_store_fallback.py`, `src/tools/search_precedents.py`, `src/pipeline/runner.py`, `configs/agents/governance-verdict.yaml`
**Migration:** None (CaseState is a Pydantic model, not a DB table)

---

## Fix 3: Case Description Field

**Problem:** A `description` field is needed for judges to annotate what a case is about. The current `CaseCreateRequest` only accepts `domain`. This is a new column addition.

**Solution:**

1. **`src/models/case.py`** — Add column to `Case` model after `domain`:
   ```python
   description: Mapped[str | None] = mapped_column(Text)
   ```
   Nullable — existing rows get `NULL`, no backfill needed.

2. **New Alembic migration** — `alembic/versions/0002_add_case_description.py`:
   ```python
   def upgrade() -> None:
       op.add_column("cases", sa.Column("description", sa.Text(), nullable=True))

   def downgrade() -> None:
       op.drop_column("cases", "description")
   ```
   Generate via `alembic revision --autogenerate -m "add case description"` and verify the output matches the hand-written model. Both upgrade and downgrade paths included.

3. **`src/api/schemas/cases.py`** — Update three schemas:
   - `CaseCreateRequest`: Add `description: str | None = Field(None, description="Free-text case description", max_length=2000)`
   - `CaseResponse`: Add `description: str | None = Field(None, description="Case description")`
   - `CaseDetailResponse`: Add `description: str | None = Field(None, description="Case description")`

   **API surface impact:** Adding `description` to `CaseResponse` means it also appears in `CaseListResponse.items` (the `GET /cases` list endpoint). This is intentional — the description is useful in list views for judges scanning their caseload. The 2000-char max_length on input keeps payload size bounded.

4. **`src/api/routes/cases.py`** — In the case creation handler, sanitize the description before persistence using the existing `sanitize_user_input()` utility:
   ```python
   from src.shared.sanitization import sanitize_user_input

   # In create_case handler:
   description = sanitize_user_input(body.description) if body.description else None
   user = Case(domain=body.domain, description=description, created_by=current_user.id)
   ```
   This prevents stored prompt-injection (if the description later enters agent prompts) and stored-XSS (if a frontend renders it naively). The `sanitize_user_input()` function already strips prompt injection patterns, XML delimiter attacks, and null bytes — it was built for exactly this use case (`sanitization.py:32`).

**Files:** `src/models/case.py`, `src/api/schemas/cases.py`, `src/api/routes/cases.py`, new migration file
**Migration:** Yes — `0002_add_case_description.py` (with downgrade)

---

## TODOS.md Update

Append the 12 deferred user stories as structured sections under a new `## Outstanding User Stories` heading:

**Group B — Judge-Facing Endpoints:**
- US-009: Flag Disputed Facts
- US-010: Evidence Gaps
- US-016: Live Precedent Search (ad-hoc)
- US-017: Knowledge Base Status
- US-023: Fairness & Bias Audit display
- US-024: Escalated Cases handling

**Group C — Real-Time & Search:**
- US-002: SSE/WebSocket pipeline status
- US-028: Advanced case search & filter

**Group D — Export & Reporting:**
- US-003: Jurisdiction validation result endpoint
- US-006: Evidence Analysis Dashboard endpoint
- US-020: Hearing Pack generation
- US-027: Case Report PDF export

---

## Verification

1. **Cookie fix:** Start FastAPI locally with `COOKIE_SECURE=false` in `.env`. Login via `/auth/login`, confirm `vc_token` cookie is set with `secure=false`. Logout via `/auth/logout`, confirm cookie is deleted cleanly. Repeat with `COOKIE_SECURE` unset (should default to `True`). Verify startup warning is emitted when `cookie_secure=False` and `log_level != "DEBUG"`.
2. **Precedent flag (8 test cases):**
   - Mock PAIR to raise `HTTPError` + vector store to raise `VectorStoreError` → assert `source_failed is True`
   - Mock PAIR to raise `HTTPError` + vector store returns `[]` → assert `source_failed is False`
   - Mock PAIR to raise `HTTPError` + vector store returns results → assert `source_failed is False`, `fallback_used is True`
   - Mock circuit breaker OPEN + vector store raises `VectorStoreError` → assert `source_failed is True`
   - Mock circuit breaker OPEN + vector store returns `[]` → assert `source_failed is True`
   - Mock circuit breaker OPEN + vector store returns results → assert `source_failed is False`, `fallback_used is True`
   - Mock PAIR returns `[]` normally → assert `source_failed is False`
   - Cache round-trip: trigger outage, verify cached payload includes metadata, hit cache, verify metadata survives
3. **Description field:** Run `alembic upgrade head`, create a case with description via API, retrieve via `GET /cases` and `GET /cases/{id}`, confirm description is present and sanitized. Test with prompt-injection payload in description (e.g., `<|im_start|>system`), confirm it is stripped. Run `alembic downgrade -1` to verify rollback. Run existing tests to confirm no regressions.
4. **TODOS.md:** Visual review — all 12 user stories present with correct IDs.
5. **Linting:** `ruff check` and `ruff format --check` pass on all modified files.
