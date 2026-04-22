"""Write a terminal CaseState back to the relational tables.

The mesh (and the sequential runner before it) emits a CaseState whose
fields carry the full agent output graph as nested dicts. The relational
tables are denormalized: one row per evidence item, fact, witness,
legal-rule, precedent, argument, etc. This module bridges the two.

Design notes:

- **Delete-then-insert per child table.** A pipeline re-run (intake retry
  or what-if regeneration) must not leave stale rows behind. Wrapping
  everything in one transaction keeps the case's DB view consistent.
- **Shape tolerance.** Agent outputs are `dict[str, Any]`; tests and
  fixtures use varying key layouts (``evidence_items`` vs ``items``,
  ``statements`` vs ``witnesses``). Helpers normalize by looking for the
  first list-of-dicts under a set of candidate keys, falling back to an
  empty list so a partial pipeline still persists what it has.
- **Fairness + judge KB** collapse into JSONB on the ``Verdict`` row
  (fairness) and the ``Case.description`` metadata is left untouched
  (no JSONB column on ``Case``; judge KB results are intentionally not
  persisted to a dedicated table yet).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.pipeline_state import persist_case_state
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
from src.shared.case_state import CaseState

logger = logging.getLogger(__name__)


async def persist_case_results(
    db: AsyncSession,
    case_id: UUID,
    state: CaseState,
) -> None:
    """Persist a terminal CaseState to the relational tables.

    Rolls back on any failure. Safe to call repeatedly — child rows for
    ``case_id`` are replaced, not appended.
    """
    try:
        await _clear_child_rows(db, case_id)
        _insert_evidence(db, case_id, state)
        _insert_facts(db, case_id, state)
        _insert_witnesses(db, case_id, state)
        _insert_legal_rules(db, case_id, state)
        _insert_precedents(db, case_id, state)
        _insert_arguments(db, case_id, state)
        _insert_deliberation(db, case_id, state)
        _insert_verdict(db, case_id, state)
        _insert_audit_log(db, case_id, state)
        await _update_case_row(db, case_id, state)
        await db.commit()
    except Exception as exc:
        logger.error("persist_case_results failed for case_id=%s: %s", case_id, exc)
        await db.rollback()
        raise

    # Write the terminal CaseState to pipeline_checkpoints so
    # What-If / stability endpoints can rehydrate it via load_case_state.
    # The mesh runner already checkpoints per-hop; the legacy in-process
    # runner doesn't, so this terminal write decouples what-if hydration
    # from which runner produced the state.
    if state.run_id:
        await persist_case_state(
            db,
            case_id=case_id,
            run_id=state.run_id,
            agent_name="terminal",
            state=state,
        )


# ---------------------------------------------------------------------------
# Orchestration helpers
# ---------------------------------------------------------------------------


async def _clear_child_rows(db: AsyncSession, case_id: UUID) -> None:
    """Delete all pipeline-produced rows for ``case_id`` before re-inserting."""
    for model in (
        Evidence,
        Fact,
        Witness,
        LegalRule,
        Precedent,
        Argument,
        Deliberation,
        Verdict,
        AuditLog,
    ):
        await db.execute(delete(model).where(model.case_id == case_id))


async def _update_case_row(
    db: AsyncSession,
    case_id: UUID,
    state: CaseState,
) -> None:
    """Sync selected Case columns (status, complexity, route) from CaseState."""
    case = await db.get(Case, case_id)
    if case is None:
        logger.warning("persist_case_results: Case %s not found, skipping row update", case_id)
        return
    case.status = _map_case_status(state.status.value)
    # Anchor What-If rehydration at the terminal run_id. The mesh runner
    # upserts a pipeline_checkpoints row for this (case_id, run_id); the
    # what-if / stability endpoints read that row back via load_case_state.
    if state.run_id:
        case.latest_run_id = state.run_id
    complexity = (state.case_metadata or {}).get("complexity")
    if complexity in {"low", "medium", "high"}:
        from src.models.case import CaseComplexity

        case.complexity = CaseComplexity(complexity)
    route = (state.case_metadata or {}).get("route")
    if route in {"proceed_automated", "proceed_with_review", "escalate_human"}:
        from src.models.case import CaseRoute

        case.route = CaseRoute(route)
    jurisdiction_valid = (state.case_metadata or {}).get("jurisdiction_valid")
    if isinstance(jurisdiction_valid, bool):
        case.jurisdiction_valid = jurisdiction_valid


# ---------------------------------------------------------------------------
# Per-entity inserters
# ---------------------------------------------------------------------------


def _insert_evidence(db: AsyncSession, case_id: UUID, state: CaseState) -> None:
    items = _items_from(state.evidence_analysis, ("evidence_items", "items", "evidence"))
    for item in items:
        ev_type = _coerce_enum(item.get("evidence_type"), EvidenceType)
        if ev_type is None:
            continue
        db.add(
            Evidence(
                case_id=case_id,
                evidence_type=ev_type,
                strength=_coerce_enum(item.get("strength"), EvidenceStrength),
                admissibility_flags=item.get("admissibility_flags"),
                linked_claims=_as_jsonb(item.get("linked_claims")),
            )
        )


def _insert_facts(db: AsyncSession, case_id: UUID, state: CaseState) -> None:
    items = _items_from(state.extracted_facts, ("facts", "items", "timeline"))
    for item in items:
        description = (item.get("description") or "").strip()
        if not description:
            continue
        db.add(
            Fact(
                case_id=case_id,
                event_date=_parse_date(item.get("event_date") or item.get("date")),
                event_time=_parse_time(item.get("event_time") or item.get("time")),
                description=description,
                confidence=_coerce_enum(item.get("confidence"), FactConfidence),
                status=_coerce_enum(item.get("status"), FactStatus),
                corroboration=_as_jsonb(item.get("corroboration")),
            )
        )


def _insert_witnesses(db: AsyncSession, case_id: UUID, state: CaseState) -> None:
    data = state.witnesses
    if not data:
        return
    items = data.witnesses or data.statements
    for item in items:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        credibility = item.get("credibility_score")
        db.add(
            Witness(
                case_id=case_id,
                name=name,
                role=item.get("role"),
                credibility_score=int(credibility)
                if isinstance(credibility, (int, float))
                else None,
                bias_indicators=_as_jsonb(item.get("bias_indicators")),
                simulated_testimony=item.get("simulated_testimony"),
            )
        )


def _insert_legal_rules(db: AsyncSession, case_id: UUID, state: CaseState) -> None:
    for item in state.legal_rules or []:
        if not isinstance(item, dict):
            continue
        statute = (item.get("statute_name") or item.get("name") or "").strip()
        if not statute:
            continue
        relevance = item.get("relevance_score")
        db.add(
            LegalRule(
                case_id=case_id,
                statute_name=statute,
                section=item.get("section"),
                verbatim_text=item.get("verbatim_text") or item.get("text"),
                relevance_score=float(relevance) if isinstance(relevance, (int, float)) else None,
                application=item.get("application"),
            )
        )


def _insert_precedents(db: AsyncSession, case_id: UUID, state: CaseState) -> None:
    for item in state.precedents or []:
        if not isinstance(item, dict):
            continue
        citation = (item.get("citation") or item.get("case_name") or "").strip()
        if not citation:
            continue
        similarity = item.get("similarity_score")
        db.add(
            Precedent(
                case_id=case_id,
                citation=citation,
                court=item.get("court"),
                outcome=item.get("outcome"),
                reasoning_summary=item.get("reasoning_summary") or item.get("reasoning"),
                similarity_score=float(similarity)
                if isinstance(similarity, (int, float))
                else None,
                distinguishing_factors=item.get("distinguishing_factors"),
                source=_coerce_enum(item.get("source"), PrecedentSource),
                url=item.get("url"),
            )
        )


def _insert_arguments(db: AsyncSession, case_id: UUID, state: CaseState) -> None:
    arguments = state.arguments or {}
    if not isinstance(arguments, dict):
        return
    # Agents emit arguments grouped by side ({"prosecution": [...], "defense": [...]})
    # OR as a flat list under a well-known key. Support both.
    for side_key, values in arguments.items():
        if not isinstance(values, list):
            continue
        side_enum = _coerce_enum(side_key, ArgumentSide)
        if side_enum is None:
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            legal_basis = (item.get("legal_basis") or item.get("claim") or "").strip()
            if not legal_basis:
                continue
            db.add(
                Argument(
                    case_id=case_id,
                    side=side_enum,
                    legal_basis=legal_basis,
                    supporting_evidence=_as_jsonb(item.get("supporting_evidence")),
                    weaknesses=item.get("weaknesses"),
                    suggested_questions=_as_jsonb(item.get("suggested_questions")),
                )
            )


def _insert_deliberation(db: AsyncSession, case_id: UUID, state: CaseState) -> None:
    data = state.deliberation
    if not data:
        return
    db.add(
        Deliberation(
            case_id=case_id,
            reasoning_chain=_as_jsonb(data.reasoning_chain),
            preliminary_conclusion=data.preliminary_conclusion,
            uncertainty_flags=_as_jsonb(data.uncertainty_flags),
            confidence_score=data.confidence_score,
        )
    )


def _insert_verdict(db: AsyncSession, case_id: UUID, state: CaseState) -> None:
    verdict = state.verdict_recommendation
    if not verdict:
        return
    rec_type = _coerce_enum(verdict.recommendation_type, RecommendationType)
    outcome = verdict.recommended_outcome.strip()
    if rec_type is None or not outcome:
        return
    db.add(
        Verdict(
            case_id=case_id,
            recommendation_type=rec_type,
            recommended_outcome=outcome,
            sentence=None,
            confidence_score=verdict.confidence_score,
            alternative_outcomes=_as_jsonb(verdict.alternative_outcomes),
            fairness_report=_as_jsonb(state.fairness_check),
        )
    )


def _insert_audit_log(db: AsyncSession, case_id: UUID, state: CaseState) -> None:
    for entry in state.audit_log or []:
        db.add(
            AuditLog(
                case_id=case_id,
                agent_name=entry.agent,
                action=entry.action,
                input_payload=_as_jsonb(entry.input_payload),
                output_payload=_as_jsonb(entry.output_payload),
                system_prompt=entry.system_prompt,
                llm_response=_as_jsonb(entry.llm_response),
                tool_calls=_as_jsonb(entry.tool_calls),
                model=entry.model,
                token_usage=_as_jsonb(entry.token_usage),
                solace_message_id=entry.solace_message_id,
            )
        )


# ---------------------------------------------------------------------------
# Coercion + shape helpers
# ---------------------------------------------------------------------------


def _items_from(container: Any, candidate_keys: Iterable[str]) -> list[dict[str, Any]]:
    """Extract a list of dicts from a variable-shape container.

    Tolerates both direct list containers and dicts-of-list layouts that
    agents use (e.g. ``{"evidence_items": [...]}``).
    """
    if isinstance(container, list):
        return [x for x in container if isinstance(x, dict)]
    if not isinstance(container, dict):
        return []
    for key in candidate_keys:
        value = container.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _coerce_enum(value: Any, enum_cls: type) -> Any | None:
    if value is None:
        return None
    try:
        return enum_cls(value)
    except (ValueError, KeyError):
        return None


def _map_case_status(value: str) -> CaseStatus:
    try:
        return CaseStatus(value)
    except ValueError:
        logger.warning("Unknown CaseStatus %r, defaulting to processing", value)
        return CaseStatus.processing


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _parse_time(value: Any) -> time | None:
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        try:
            return time.fromisoformat(value)
        except ValueError:
            return None
    return None


def _as_jsonb(value: Any) -> Any | None:
    """Normalize to something SQLAlchemy's JSONB column accepts."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    try:
        return dict(value)
    except (TypeError, ValueError):
        return None
