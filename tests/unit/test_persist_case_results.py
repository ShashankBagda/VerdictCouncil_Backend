"""Unit tests for `src.db.persist_case_results`.

The goal of this module is shape tolerance + correct row construction —
both exercised with an AsyncMock-style session that captures every
`db.add(obj)` and `db.execute(stmt)`. We do not stand up a real DB
here; `tests/integration` covers round-trip behaviour against Postgres.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.db.persist_case_results import persist_case_results
from src.models.audit import AuditLog
from src.models.case import (
    Argument,
    ArgumentSide,
    Case,
    CaseStatus,
    Deliberation,
    Evidence,
    EvidenceStrength,
    EvidenceType,
    Fact,
    FactConfidence,
    FactStatus,
    LegalRule,
    Precedent,
    PrecedentSource,
    RecommendationType,
    Verdict,
    Witness,
)
from src.shared.case_state import AuditEntry, CaseState, CaseStatusEnum


# ---------------------------------------------------------------------------
# Test double
# ---------------------------------------------------------------------------


class _RecordingSession:
    """Pretends to be an AsyncSession just enough for persist_case_results.

    Captures every `.add()` call plus the SQL statements passed to
    `.execute()`. `commit` / `rollback` are recorded as flags so tests
    can assert the transaction path was taken.
    """

    def __init__(self, case: Case | None = None) -> None:
        self.added: list[Any] = []
        self.executed: list[Any] = []
        self._case = case
        self.committed = False
        self.rolled_back = False

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def execute(self, statement: Any) -> None:
        self.executed.append(statement)

    async def get(self, model: type, pk: Any) -> Any:
        if model is Case:
            return self._case
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def _case(case_id=None) -> Any:
    """Stand-in Case that accepts attribute writes without SQLAlchemy instrumentation."""
    case = MagicMock(spec=Case)
    case.id = case_id or uuid4()
    case.status = CaseStatus.processing
    case.complexity = None
    case.route = None
    return case


def _added_of(session: _RecordingSession, model: type) -> list[Any]:
    return [obj for obj in session.added if isinstance(obj, model)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commits_and_clears_all_child_tables_first():
    case_id = uuid4()
    case = _case(case_id)
    state = CaseState(case_id=str(case_id), status=CaseStatusEnum.decided)

    session = _RecordingSession(case=case)
    await persist_case_results(session, case_id, state)  # type: ignore[arg-type]

    assert session.committed is True
    assert session.rolled_back is False
    # 9 child tables cleared (Evidence, Fact, Witness, LegalRule, Precedent,
    # Argument, Deliberation, Verdict, AuditLog)
    assert len(session.executed) == 9


@pytest.mark.asyncio
async def test_maps_evidence_items_from_evidence_analysis_dict():
    case_id = uuid4()
    state = CaseState(
        case_id=str(case_id),
        evidence_analysis={
            "evidence_items": [
                {
                    "evidence_type": "documentary",
                    "strength": "strong",
                    "description": "Contract PDF",
                    "admissibility_flags": {"hearsay": False},
                    "linked_claims": ["c1", "c2"],
                },
                # Missing evidence_type → skipped
                {"description": "orphan"},
                # Unknown evidence_type → skipped
                {"evidence_type": "rumor"},
            ]
        },
    )

    session = _RecordingSession(case=_case(case_id))
    await persist_case_results(session, case_id, state)  # type: ignore[arg-type]

    evidence = _added_of(session, Evidence)
    assert len(evidence) == 1
    assert evidence[0].evidence_type == EvidenceType.documentary
    assert evidence[0].strength == EvidenceStrength.strong
    assert evidence[0].admissibility_flags == {"hearsay": False}
    assert evidence[0].linked_claims == ["c1", "c2"]


@pytest.mark.asyncio
async def test_parses_fact_dates_and_skips_empty_descriptions():
    case_id = uuid4()
    state = CaseState(
        case_id=str(case_id),
        extracted_facts={
            "facts": [
                {
                    "description": "Accident occurred at 14:30",
                    "event_date": "2026-03-15",
                    "event_time": "14:30:00",
                    "confidence": "high",
                    "status": "agreed",
                },
                {"description": ""},  # skipped
                {"description": "Bad date", "event_date": "not-a-date"},  # parsed as None
            ]
        },
    )

    session = _RecordingSession(case=_case(case_id))
    await persist_case_results(session, case_id, state)  # type: ignore[arg-type]

    facts = _added_of(session, Fact)
    assert len(facts) == 2
    assert facts[0].event_date == date(2026, 3, 15)
    assert facts[0].event_time == time(14, 30, 0)
    assert facts[0].confidence == FactConfidence.high
    assert facts[0].status == FactStatus.agreed
    assert facts[1].event_date is None


@pytest.mark.asyncio
async def test_witnesses_require_name_and_clamp_credibility():
    case_id = uuid4()
    state = CaseState(
        case_id=str(case_id),
        witnesses={
            "statements": [
                {"name": "Alice", "role": "officer", "credibility_score": 80},
                {"name": "", "role": "unknown"},  # skipped
                {"name": "Bob", "credibility_score": "not-a-number"},  # credibility → None
            ]
        },
    )
    session = _RecordingSession(case=_case(case_id))
    await persist_case_results(session, case_id, state)  # type: ignore[arg-type]

    witnesses = _added_of(session, Witness)
    assert [w.name for w in witnesses] == ["Alice", "Bob"]
    assert witnesses[0].credibility_score == 80
    assert witnesses[1].credibility_score is None


@pytest.mark.asyncio
async def test_legal_rules_and_precedents_mapped_directly():
    case_id = uuid4()
    state = CaseState(
        case_id=str(case_id),
        legal_rules=[
            {"statute_name": "RTA s.64", "section": "64(1)", "relevance_score": 0.9},
            {"statute_name": ""},  # skipped
        ],
        precedents=[
            {
                "citation": "Smith v. State",
                "court": "HC",
                "source": "curated",
                "similarity_score": 0.75,
            },
            {"citation": ""},  # skipped
        ],
    )

    session = _RecordingSession(case=_case(case_id))
    await persist_case_results(session, case_id, state)  # type: ignore[arg-type]

    rules = _added_of(session, LegalRule)
    assert len(rules) == 1
    assert rules[0].statute_name == "RTA s.64"
    assert rules[0].relevance_score == pytest.approx(0.9)

    precedents = _added_of(session, Precedent)
    assert len(precedents) == 1
    assert precedents[0].source == PrecedentSource.curated
    assert precedents[0].similarity_score == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_arguments_grouped_by_side():
    case_id = uuid4()
    state = CaseState(
        case_id=str(case_id),
        arguments={
            "prosecution": [
                {"legal_basis": "S.64(1) violated", "weaknesses": "none"},
            ],
            "defense": [
                {"legal_basis": "Jurisdiction unclear"},
                {"legal_basis": ""},  # skipped
            ],
            "unknown_side": [{"legal_basis": "ignored"}],  # unknown side → skipped
        },
    )
    session = _RecordingSession(case=_case(case_id))
    await persist_case_results(session, case_id, state)  # type: ignore[arg-type]

    args = _added_of(session, Argument)
    sides = {a.side for a in args}
    assert sides == {ArgumentSide.prosecution, ArgumentSide.defense}
    assert len(args) == 2


@pytest.mark.asyncio
async def test_deliberation_and_verdict_single_row_each():
    case_id = uuid4()
    state = CaseState(
        case_id=str(case_id),
        deliberation={
            "reasoning_chain": [{"step": 1, "claim": "Acted negligently"}],
            "preliminary_conclusion": "Guilty",
            "confidence_score": 82,
        },
        verdict_recommendation={
            "recommendation_type": "guilty",
            "recommended_outcome": "Fine $500",
            "confidence_score": 85,
            "alternative_outcomes": [{"outcome": "Warning", "reasoning": "mitigation"}],
        },
        fairness_check={"critical_issues_found": False, "audit_passed": True},
    )

    session = _RecordingSession(case=_case(case_id))
    await persist_case_results(session, case_id, state)  # type: ignore[arg-type]

    deliberations = _added_of(session, Deliberation)
    verdicts = _added_of(session, Verdict)
    assert len(deliberations) == 1
    assert deliberations[0].confidence_score == 82
    assert len(verdicts) == 1
    assert verdicts[0].recommendation_type == RecommendationType.guilty
    assert verdicts[0].recommended_outcome == "Fine $500"
    assert verdicts[0].fairness_report == {
        "critical_issues_found": False,
        "audit_passed": True,
    }


@pytest.mark.asyncio
async def test_audit_entries_persist_one_row_each():
    case_id = uuid4()
    state = CaseState(
        case_id=str(case_id),
        audit_log=[
            AuditEntry(
                agent="case-processing",
                action="parse_documents",
                model="gpt-5.4-nano",
                token_usage={"prompt": 500, "completion": 200},
            ),
            AuditEntry(agent="governance-verdict", action="emit_verdict"),
        ],
    )
    session = _RecordingSession(case=_case(case_id))
    await persist_case_results(session, case_id, state)  # type: ignore[arg-type]

    logs = _added_of(session, AuditLog)
    assert [a.agent_name for a in logs] == ["case-processing", "governance-verdict"]
    assert logs[0].token_usage == {"prompt": 500, "completion": 200}


@pytest.mark.asyncio
async def test_case_row_status_and_metadata_sync():
    case_id = uuid4()
    case = _case(case_id)
    state = CaseState(
        case_id=str(case_id),
        status=CaseStatusEnum.escalated,
        case_metadata={"complexity": "high", "route": "escalate_human"},
    )

    session = _RecordingSession(case=case)
    await persist_case_results(session, case_id, state)  # type: ignore[arg-type]

    from src.models.case import CaseComplexity, CaseRoute

    assert case.status == CaseStatus.escalated
    assert case.complexity == CaseComplexity.high
    assert case.route == CaseRoute.escalate_human


@pytest.mark.asyncio
async def test_rolls_back_on_failure():
    case_id = uuid4()
    state = CaseState(case_id=str(case_id))

    session = _RecordingSession(case=_case(case_id))

    async def _boom(_statement: Any) -> None:
        raise RuntimeError("delete blew up")

    session.execute = _boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        await persist_case_results(session, case_id, state)  # type: ignore[arg-type]

    assert session.rolled_back is True
    assert session.committed is False
