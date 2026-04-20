# Changelog

All notable changes to VerdictCouncil Backend are documented here.

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
