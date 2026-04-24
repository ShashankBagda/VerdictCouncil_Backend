# Changelog

All notable changes to VerdictCouncil Backend are documented here.

## [0.3.0] - 2026-04-21

### Changed (Breaking)

- **4-gate HITL pipeline** — pipeline pauses after each of four gates for judge review before proceeding. Gate state (`awaiting_review_gate1` through `awaiting_review_gate4`) is written atomically to `cases.gate_state` JSONB.
- **Agent 8 renamed**: `deliberation` → `hearing-analysis`. Config: `configs/agents/hearing-analysis.yaml`. Owned field: `hearing_analyses` (list of per-run hearing analysis objects). No longer produces a `deliberation` field.
- **Agent 9 renamed**: `governance-verdict` → `hearing-governance`. Config: `configs/agents/hearing-governance.yaml`. Updates `hearing_analyses[-1].preliminary_conclusion` and sets final `status`. Does not produce a verdict recommendation.
- `Procfile.dev` updated to use renamed agent configs.

### Added

- `pipeline_checkpoints` table — per-gate state snapshots keyed `(case_id, run_id={case_id}-{gate_name})`, enabling What-If replay from any gate.
- `cases.gate_state` JSONB column — atomic gate pause state written in the same DB transaction as all other case updates.
- `cases.judicial_decision` JSONB column — judge records their own decision after Gate 4 review.

### Removed

- `verdicts` table and associated Alembic migration.
- `decisions` API route group (`/api/v1/decisions/*`) — superseded by `cases.judicial_decision`.
- `escalated-cases` route group — escalation at Gate 1 is now surfaced through the standard cases list with `status = escalated`.
- `senior-inbox` route group — the multi-judge senior review workflow has been removed.

---

## [0.2.0] - 2026-04-15

### Changed (Breaking)

- **Responsible AI refactor** — removed all AI verdict recommendation machinery. The system is now advisory only: it produces structured analysis at each gate but does not recommend a verdict.
- `fairness_check` and `verdict_recommendation` CaseState fields removed. Governance outputs are now folded into `hearing_analyses[-1]`.
- `confidence_calc` tool removed from `hearing-governance` agent (previously `governance-verdict`).

### Added

- `cases.judicial_decision` JSONB — replaces the `verdicts` table as the canonical store for judge decisions.

---

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
