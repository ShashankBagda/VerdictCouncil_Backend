# Changelog

All notable changes to VerdictCouncil Backend are documented here.

## [0.2.0.0] - 2026-04-15

### Added

- **Per-judge knowledge base** — Each judge gets their own OpenAI Vector Store. Upload documents, search by semantic query, and have KB results automatically injected into the pipeline for judge-specific context. 5 new endpoints under `/api/v1/knowledge-base/`.
- **Structured output schemas** — Governance-verdict agent now uses OpenAI's strict JSON schema mode with fully specified Pydantic models. Other agents validate outputs post-parse. No more silent schema mismatches.
- **Layered input guardrails** — Documents and case descriptions are scanned for prompt injection: fast regex with unicode normalization catches known patterns, a lightweight LLM check escalates ambiguous cases. Injection-detected content is sanitized before entering the pipeline.
- **Output integrity gate** — After governance-verdict, the pipeline checks for missing fields, out-of-range confidence scores, and absent fairness audits. Failures halt the pipeline and escalate the case to human review instead of silently continuing.
- **Retry-then-fail-closed** — If an agent returns non-JSON, the pipeline retries once with the same parameters. Second failure raises `RuntimeError` and halts. No more silent `{}` fallbacks.
- **Document upload with file storage** — `POST /api/v1/cases/{case_id}/documents` now reads file bytes, validates type (PDF, PNG, JPEG, TXT, DOC/DOCX) and size (50 MB limit), uploads to OpenAI Files API, and stores the `openai_file_id` for pipeline processing.
- **Post-decision calibration tracking** — Every judge decision records a `CalibrationRecord` with divergence scoring (accept=0.0, modify=0.5, reject=1.0). New `GET /api/v1/dashboard/calibration` endpoint exposes aggregate stats.
- **Pipeline eval framework** — 3 gold-set test cases (refund dispute, service complaint, traffic appeal) with scoring on completeness, verdict quality, and fairness check presence. Gated behind `@pytest.mark.eval`.

### Changed

- **Token accounting** — Token usage now accumulates across multi-turn tool-call conversations instead of being overwritten on each turn.
- **Injection detection hardened** — Unicode normalization (NFKD + zero-width stripping) defeats homoglyph bypasses. Delimiter matching uses fixed strings instead of regex where possible. NL patterns tightened to reduce false positives on legitimate legal text.
- **Decision endpoint ownership** — Judges can only record decisions on cases they created. Prevents cross-judge access in future multi-judge deployments.
- **Verdict ordering** — Fairness audit query uses `created_at DESC, id DESC` for deterministic ordering when timestamps match.
- **Agent prompt clarity** — 4 agent configs (argument-construction, deliberation, governance-verdict, witness-analysis) now explicitly reference domain enum values in their prompt instructions.

### Fixed

- **Verdict ordering by UUID** — Previously ordered by random UUID v4 `id`. Now uses `created_at` with `id` tiebreaker. (Resolves TODOS.md tech debt item.)

## [0.1.0.0] - 2026-04-06

### Added

- **Judge dispute workflow** — `PATCH /api/v1/cases/{case_id}/facts/{fact_id}/dispute` lets judges flag facts as disputed with a required reason. Returns 409 if fact is already disputed.
- **Evidence gaps** — `GET /api/v1/cases/{case_id}/evidence-gaps` surfaces weak evidence and uncorroborated facts for a case. Disputed facts are excluded from the uncorroborated list (they have their own workflow).
- **Fairness & bias audit** — `GET /api/v1/cases/{case_id}/fairness-audit` exposes the governance agent's fairness check output and the verdict-level fairness report in a single view.
- **Ad-hoc precedent search** — `POST /api/v1/precedents/search` lets judges run live PAIR API searches outside the pipeline, with automatic vector store fallback and sanitized query/jurisdiction inputs.
- **Knowledge base status** — `GET /api/v1/knowledge-base/status` returns the PAIR circuit breaker state and OpenAI vector store health. Client errors are logged server-side; the response returns a normalized message.
- **Escalated case management** — `GET /api/v1/escalated-cases/` lists cases pending human review with pagination. `POST /api/v1/escalated-cases/{case_id}/action` supports four actions: `add_notes`, `return_to_pipeline`, `manual_decision` (creates a Verdict record), and `reject`.
- **`manual_decision` recommendation type** — new enum value added to `RecommendationType` with migration `0003_add_manual_decision_recommendation_type`.

### Changed

- All six new endpoints require `judge` role — clerks receive 403.
- OpenAI client for vector store health checks is now a module-level lazy singleton instead of per-request instantiation.
