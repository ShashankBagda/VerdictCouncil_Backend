"""Senior judge inbox aggregation and action endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.api.deps import DBSession, require_role
from src.api.schemas.workflows import (
    SeniorInboxAction,
    SeniorInboxActionRequest,
    SeniorInboxActionResponse,
)
from src.models.audit import AuditLog
from src.models.case import (
    Case,
    CaseStatus,
    RecommendationType,
    ReopenRequest,
    ReopenRequestStatus,
    Verdict,
)
from src.models.user import User, UserRole

router = APIRouter()


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _sort_key(item: dict[str, Any]) -> tuple[int, str]:
    priority_order = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    return (priority_order.get(item.get("priority", "low"), 4), item.get("submitted_at") or "")


def _history_entry(action: str, actor: str, created_at: datetime | None, reason: str | None, *, assignee: str | None = None) -> dict[str, Any]:
    return {
        "action": action,
        "reason": reason,
        "actor": actor,
        "created_at": created_at,
        "assignee": assignee,
    }


def _latest_matching_log(
    audit_logs: list[AuditLog],
    *,
    action_prefix: str,
    request_id: str | None = None,
) -> AuditLog | None:
    matches: list[AuditLog] = []
    for log in audit_logs:
        if not log.action.startswith(action_prefix):
            continue
        if request_id is not None and (log.input_payload or {}).get("request_id") != request_id:
            continue
        matches.append(log)
    if not matches:
        return None
    return max(matches, key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC))


def _serialize_escalation_item(case: Case, current_user: User) -> dict[str, Any] | None:
    latest_action = _latest_matching_log(
        list(case.audit_logs or []), action_prefix="senior_inbox_escalation_"
    )
    if latest_action is not None:
        last_action = latest_action.action.removeprefix("senior_inbox_escalation_")
        assignee = _optional_text((latest_action.input_payload or {}).get("assignee"))
        if last_action in {"approve", "reject", "request_more_info"}:
            return None
        if last_action == "reassign" and assignee and assignee != current_user.email:
            return None

    reason = "Escalated for senior review."
    for log in sorted(
        case.audit_logs or [],
        key=lambda item: item.created_at.timestamp() if item.created_at else 0.0,
        reverse=True,
    ):
        if "escalat" not in log.action.lower():
            continue
        for payload in (log.output_payload or {}, log.input_payload or {}):
            for key in ("reason", "escalation_reason", "summary", "detail", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    reason = value
                    break
            if reason != "Escalated for senior review.":
                break

    history = [
        _history_entry(
            log.action,
            str((log.input_payload or {}).get("judge_id") or log.agent_name),
            log.created_at,
            (log.input_payload or {}).get("notes") or (log.input_payload or {}).get("reason"),
            assignee=(log.input_payload or {}).get("assignee"),
        )
        for log in sorted(
            case.audit_logs or [],
            key=lambda item: item.created_at.timestamp() if item.created_at else 0.0,
        )
        if "escalat" in log.action.lower() or log.action.startswith("senior_inbox_escalation_")
    ]

    return {
        "id": f"escalation:{case.id}",
        "case_id": str(case.id),
        "item_type": "escalation",
        "originating_judge": str(case.created_by),
        "reason": reason,
        "priority": "urgent" if case.complexity and case.complexity.value == "high" else "high",
        "submitted_at": (case.updated_at or case.created_at).isoformat()
        if (case.updated_at or case.created_at)
        else None,
        "status": "pending",
        "preview": _optional_text(case.description) or _optional_text(case.title) or reason,
        "case_title": _optional_text(case.title) or f"Case {case.id}",
        "domain": case.domain.value,
        "history": history,
    }


def _serialize_reopen_item(request_item: ReopenRequest, current_user: User) -> dict[str, Any] | None:
    latest_action = _latest_matching_log(
        list(request_item.case.audit_logs or []),
        action_prefix="senior_inbox_reopen_",
        request_id=str(request_item.id),
    )
    assignee = _optional_text((latest_action.input_payload or {}).get("assignee")) if latest_action else None
    if latest_action is not None:
        last_action = latest_action.action.removeprefix("senior_inbox_reopen_")
        if last_action in {"approve", "reject", "request_more_info"}:
            return None
        if last_action == "reassign" and assignee and assignee != current_user.email:
            return None

    case = request_item.case
    history = [
        _history_entry(
            "reopen_request_create",
            str(request_item.requested_by),
            request_item.created_at,
            request_item.justification,
        )
    ]
    for log in sorted(case.audit_logs or [], key=lambda item: item.created_at.timestamp() if item.created_at else 0.0):
        if not log.action.startswith("senior_inbox_reopen_"):
            continue
        if (log.input_payload or {}).get("request_id") != str(request_item.id):
            continue
        history.append(
            _history_entry(
                log.action,
                str((log.input_payload or {}).get("senior_judge_id") or log.agent_name),
                log.created_at,
                (log.input_payload or {}).get("reason"),
                assignee=(log.input_payload or {}).get("assignee"),
            )
        )

    return {
        "id": f"reopen:{request_item.id}",
        "case_id": str(request_item.case_id),
        "item_type": "reopen",
        "originating_judge": str(request_item.requested_by),
        "reason": request_item.reason,
        "priority": "urgent" if request_item.reason in {"appeal", "clerical_error"} else "medium",
        "submitted_at": request_item.created_at.isoformat() if request_item.created_at else None,
        "status": request_item.status.value,
        "preview": _optional_text(request_item.justification),
        "case_title": ((_optional_text(case.title) if case else None) or f"Case {request_item.case_id}"),
        "domain": case.domain.value if case and case.domain else None,
        "history": history,
        "assignee": assignee,
    }


def _collect_pending_amendments(case: Case, current_user: User) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    audit_logs = sorted(case.audit_logs or [], key=lambda item: item.created_at.timestamp() if item.created_at else 0.0)
    for log in audit_logs:
        if log.action != "decision_amendment_request":
            continue
        payload = log.input_payload or {}
        request_id = _optional_text(payload.get("request_id"))
        if not request_id:
            continue
        latest_action = _latest_matching_log(
            audit_logs,
            action_prefix="senior_inbox_amendment_",
            request_id=request_id,
        )
        assignee = _optional_text((latest_action.input_payload or {}).get("assignee")) if latest_action else None
        if latest_action is not None:
            last_action = latest_action.action.removeprefix("senior_inbox_amendment_")
            if last_action in {"approve", "reject", "request_more_info"}:
                continue
            if last_action == "reassign" and assignee and assignee != current_user.email:
                continue

        history = [
            _history_entry(
                "decision_amendment_request",
                str(payload.get("requested_by") or "judge"),
                log.created_at,
                payload.get("reason"),
            )
        ]
        for review_log in audit_logs:
            if not review_log.action.startswith("senior_inbox_amendment_"):
                continue
            if (review_log.input_payload or {}).get("request_id") != request_id:
                continue
            history.append(
                _history_entry(
                    review_log.action,
                    str((review_log.input_payload or {}).get("senior_judge_id") or review_log.agent_name),
                    review_log.created_at,
                    (review_log.input_payload or {}).get("reason"),
                    assignee=(review_log.input_payload or {}).get("assignee"),
                )
            )

        items.append(
            {
                "id": f"amendment:{request_id}",
                "case_id": str(case.id),
                "item_type": "amendment",
                "originating_judge": str(payload.get("requested_by") or case.created_by),
                "reason": payload.get("reason") or "Decision amendment awaiting senior review.",
                "priority": "medium",
                "submitted_at": log.created_at.isoformat() if log.created_at else None,
                "status": "pending",
                "preview": _optional_text(payload.get("final_order")),
                "case_title": _optional_text(case.title) or f"Case {case.id}",
                "domain": case.domain.value if case.domain else None,
                "history": history,
                "requested_change": payload.get("final_order"),
                "assignee": assignee,
            }
        )
    return items


def _parse_item_id(item_id: str) -> tuple[str, str]:
    item_type, _, raw_id = str(item_id).partition(":")
    if not item_type or not raw_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid senior inbox item id",
        )
    return item_type, raw_id


@router.get(
    "/",
    operation_id="list_senior_inbox",
    summary="List senior judge inbox items",
    description="Aggregates escalations, decision amendments, and pending reopen requests.",
)
async def list_senior_inbox(
    db: DBSession,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = require_role(UserRole.senior_judge),
) -> dict:
    all_cases = (
        (
            await db.execute(
                select(Case).options(
                    selectinload(Case.audit_logs),
                    selectinload(Case.verdicts),
                )
            )
        )
        .scalars()
        .all()
    )

    pending_reopen = (
        (
            await db.execute(
                select(ReopenRequest)
                .where(ReopenRequest.status == ReopenRequestStatus.pending)
                .options(selectinload(ReopenRequest.case).selectinload(Case.audit_logs))
            )
        )
        .scalars()
        .all()
    )

    items = [
        *[
            item
            for case in all_cases
            if case.status == CaseStatus.escalated
            for item in [_serialize_escalation_item(case, current_user)]
            if item is not None
        ],
        *[
            item
            for request_item in pending_reopen
            for item in [_serialize_reopen_item(request_item, current_user)]
            if item is not None
        ],
        *[item for case in all_cases for item in _collect_pending_amendments(case, current_user)],
    ]

    items.sort(key=_sort_key)
    total = len(items)
    start = (page - 1) * per_page
    paginated = items[start : start + per_page]

    counts = {
        "escalation": len([item for item in items if item["item_type"] == "escalation"]),
        "reopen": len([item for item in items if item["item_type"] == "reopen"]),
        "amendment": len([item for item in items if item["item_type"] == "amendment"]),
    }

    return {
        "items": paginated,
        "total": total,
        "page": page,
        "per_page": per_page,
        "counts": counts,
    }


@router.post(
    "/{item_id}/action",
    response_model=SeniorInboxActionResponse,
    operation_id="take_senior_inbox_action",
    summary="Take action on a senior inbox item",
)
async def take_senior_inbox_action(
    item_id: str,
    body: SeniorInboxActionRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.senior_judge),
) -> SeniorInboxActionResponse:
    item_type, raw_id = _parse_item_id(item_id)
    reviewed_at = datetime.now(UTC)

    if body.action in {SeniorInboxAction.reject, SeniorInboxAction.request_more_info} and not _optional_text(body.reason):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A written reason is required for this action",
        )
    if body.action == SeniorInboxAction.reassign and not _optional_text(body.assignee):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Assignee is required for reassign",
        )

    if item_type == "escalation":
        case = (
            await db.execute(
                select(Case)
                .where(Case.id == UUID(raw_id))
                .options(selectinload(Case.audit_logs))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if case is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escalation not found")
        if case.created_by == current_user.id and body.action == SeniorInboxAction.approve:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Two-person rule: cannot approve your own referral",
            )
        if body.action == SeniorInboxAction.approve:
            case.status = CaseStatus.ready_for_review
        db.add(
            AuditLog(
                case_id=case.id,
                agent_name="judge",
                action=f"senior_inbox_escalation_{body.action.value}",
                input_payload={
                    "senior_judge_id": str(current_user.id),
                    "reason": body.reason,
                    "assignee": body.assignee,
                },
                output_payload={"status": "approved" if body.action == SeniorInboxAction.approve else "pending"},
            )
        )
        await db.commit()
        return SeniorInboxActionResponse(
            item_id=item_id,
            action=body.action,
            status="approved" if body.action == SeniorInboxAction.approve else "pending",
            message="Senior inbox action recorded.",
            assignee=body.assignee,
            reviewed_at=reviewed_at,
        )

    if item_type == "reopen":
        request_item = (
            await db.execute(
                select(ReopenRequest)
                .where(ReopenRequest.id == UUID(raw_id))
                .options(selectinload(ReopenRequest.case).selectinload(Case.audit_logs))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if request_item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reopen request not found")
        if request_item.requested_by == current_user.id and body.action == SeniorInboxAction.approve:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Two-person rule: cannot approve your own reopen request",
            )

        status_value = "pending"
        if body.action == SeniorInboxAction.approve:
            request_item.status = ReopenRequestStatus.approved
            request_item.reviewed_by = current_user.id
            request_item.review_notes = body.reason
            request_item.reviewed_at = reviewed_at
            request_item.case.status = CaseStatus.processing
            from src.models.pipeline_job import PipelineJobType
            from src.workers.outbox import enqueue_outbox_job

            await enqueue_outbox_job(
                db,
                case_id=request_item.case_id,
                job_type=PipelineJobType.case_pipeline,
                payload={"resume_from_stage": "evidence-analysis", "resume_reason": "senior_reopen_approved"},
            )
            status_value = "approved"
        elif body.action == SeniorInboxAction.reject:
            request_item.status = ReopenRequestStatus.rejected
            request_item.reviewed_by = current_user.id
            request_item.review_notes = body.reason
            request_item.reviewed_at = reviewed_at
            status_value = "rejected"

        db.add(
            AuditLog(
                case_id=request_item.case_id,
                agent_name="judge",
                action=f"senior_inbox_reopen_{body.action.value}",
                input_payload={
                    "request_id": raw_id,
                    "senior_judge_id": str(current_user.id),
                    "reason": body.reason,
                    "assignee": body.assignee,
                },
                output_payload={"status": status_value},
            )
        )
        await db.commit()
        return SeniorInboxActionResponse(
            item_id=item_id,
            action=body.action,
            status=status_value,
            message="Senior inbox action recorded.",
            assignee=body.assignee,
            reviewed_at=reviewed_at,
        )

    if item_type == "amendment":
        all_cases = (
            (
                await db.execute(
                    select(Case).options(selectinload(Case.audit_logs), selectinload(Case.verdicts))
                )
            )
            .scalars()
            .all()
        )
        matched_case: Case | None = None
        request_log: AuditLog | None = None
        for case in all_cases:
            for log in case.audit_logs or []:
                if log.action != "decision_amendment_request":
                    continue
                if (log.input_payload or {}).get("request_id") == raw_id:
                    matched_case = case
                    request_log = log
                    break
            if matched_case is not None:
                break
        if matched_case is None or request_log is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Amendment request not found")

        payload = request_log.input_payload or {}
        if str(payload.get("requested_by")) == str(current_user.id) and body.action == SeniorInboxAction.approve:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Two-person rule: cannot approve your own amendment request",
            )

        status_value = "pending"
        verdict_id: str | None = None
        if body.action == SeniorInboxAction.approve:
            base_verdict = None
            base_verdict_id = payload.get("base_verdict_id")
            if base_verdict_id:
                for verdict in matched_case.verdicts or []:
                    if str(verdict.id) == str(base_verdict_id):
                        base_verdict = verdict
                        break
            amended_verdict = Verdict(
                case_id=matched_case.id,
                recommendation_type=(
                    base_verdict.recommendation_type
                    if base_verdict
                    else matched_case.verdicts[-1].recommendation_type
                    if matched_case.verdicts
                    else RecommendationType.manual_decision
                ),
                recommended_outcome=payload.get("final_order") or "",
                sentence=base_verdict.sentence if base_verdict else None,
                confidence_score=base_verdict.confidence_score if base_verdict else None,
                alternative_outcomes=base_verdict.alternative_outcomes if base_verdict else None,
                fairness_report=base_verdict.fairness_report if base_verdict else None,
                amendment_of=base_verdict.id if base_verdict else None,
                amendment_reason=f"{payload.get('amendment_type')}: {payload.get('reason')}",
                amended_by=UUID(str(payload.get("requested_by"))),
            )
            db.add(amended_verdict)
            await db.flush()
            verdict_id = str(amended_verdict.id)
            status_value = "approved"
        elif body.action == SeniorInboxAction.reject:
            status_value = "rejected"

        db.add(
            AuditLog(
                case_id=matched_case.id,
                agent_name="judge",
                action=f"senior_inbox_amendment_{body.action.value}",
                input_payload={
                    "request_id": raw_id,
                    "senior_judge_id": str(current_user.id),
                    "reason": body.reason,
                    "assignee": body.assignee,
                },
                output_payload={"status": status_value, "verdict_id": verdict_id},
            )
        )
        await db.commit()
        return SeniorInboxActionResponse(
            item_id=item_id,
            action=body.action,
            status=status_value,
            message="Senior inbox action recorded.",
            assignee=body.assignee,
            reviewed_at=reviewed_at,
        )

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unsupported senior inbox item")
