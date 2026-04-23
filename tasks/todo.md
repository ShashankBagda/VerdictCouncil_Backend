# Remove Verdict Machinery — Responsible AI Refactor

## Context
The system must not produce judicial decisions or verdict recommendations. It supports the judge
*for a hearing* only. AI verdict recommendations with accept/modify/reject UI induce automation
bias. This refactor scopes the system to hearing preparation only.

## Decisions
- **Statuses removed**: `decided`, `rejected` → existing rows mapped to `closed` via migration
- **Statuses kept**: `ready_for_review` (renamed meaning: "AI analysis complete, ready for hearing")
- **Rename**: `deliberation` → `hearing_analysis` everywhere (agent, CaseState field, DB table)
- **Rename**: `governance-verdict` → `hearing-governance` (fairness audit only, no verdict phase)
- **Migration**: verdicts table dropped; deliberations renamed to hearing_analyses; casestatus enum updated
- **Reopen**: kept, `appeal` reason removed (no verdict to appeal)

## Tasks

### Backend
- [ ] 1. `src/shared/case_state.py` — remove VerdictRecommendation, AlternativeOutcome, judge_decision; remove decided/rejected; rename deliberation→hearing_analysis
- [ ] 2. `src/models/case.py` — remove Verdict model, RecommendationType; remove decided/rejected from CaseStatus; rename Deliberation→HearingAnalysis (__tablename__="hearing_analyses")
- [ ] 3. `configs/agents/deliberation.yaml` → `hearing-analysis.yaml` — reframe step 7 from conclusion to hearing issues
- [ ] 4. `configs/agents/governance-verdict.yaml` → `hearing-governance.yaml` — remove Phase 2 verdict generation
- [ ] 5. `src/pipeline/runner.py` — update AGENT_ORDER, halt conditions, remove verdict refs
- [ ] 6. `src/db/persist_case_results.py` — remove _insert_verdict, remove Verdict from _clear_child_rows
- [ ] 7. Delete `src/api/routes/decisions.py`
- [ ] 8. `src/api/app.py` — remove decisions router, update description
- [ ] 9. `src/api/routes/senior_inbox.py` — remove amendment handling
- [ ] 10. `src/api/routes/cases.py` — remove review_rejected_case, verdict refs, update status groups
- [ ] 11. `src/api/routes/case_data.py` — remove get_case_verdict, rename deliberation→hearing_analysis
- [ ] 12. Delete `src/api/schemas/decisions.py`
- [ ] 13. `src/api/schemas/workflows.py` — remove amendment + rejection schemas
- [ ] 14. `src/api/schemas/cases.py` — remove VerdictResponse, verdict fields from responses
- [ ] 15. `alembic/versions/0016_remove_verdict_machinery.py` — DB migration
- [ ] 16. Delete `tests/unit/test_decisions.py` + update impacted tests
- [ ] 17. `TODOS.md` — remove verdict backlog items

## Done
