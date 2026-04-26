"""What-If scenario API endpoints for Contestable Judgment Mode."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.api.deps import DBSession, require_role
from src.api.schemas.common import ErrorResponse, ValidationErrorResponse
from src.api.schemas.what_if import (
    StabilityRequest,
    StabilityResponse,
    StabilityResultResponse,
    WhatIfRequest,
    WhatIfResponse,
    WhatIfResultResponse,
)
from src.models.case import Case, CaseStatus
from src.models.user import User, UserRole
from src.models.what_if import (
    ScenarioStatus,
    StabilityClassification,
    StabilityScore,
    StabilityStatus,
    WhatIfResult,
    WhatIfScenario,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Background task helpers
# --------------------------------------------------------------------------- #


async def _run_whatif_scenario(
    scenario_id: uuid.UUID,
    *,
    trace_id: str | None = None,  # noqa: ARG001
) -> None:
    """Background task that executes the what-if scenario.

    Imports are deferred to avoid circular dependencies and to create
    a fresh database session for the background task.

    `trace_id` is accepted from the worker boundary but not yet threaded
    through `WhatIfController`; the controller wiring lands in Sprint 4
    A5 alongside the rest of the what-if observability work.
    """
    from src.db.pipeline_state import (
        CheckpointCorruptError,
        CheckpointSchemaMismatchError,
        load_case_state,
    )
    from src.pipeline.graph.runner import GraphPipelineRunner
    from src.services.database import async_session
    from src.services.whatif_controller.controller import WhatIfController
    from src.services.whatif_controller.diff_engine import generate_diff

    async with async_session() as db:
        try:
            result = await db.execute(
                select(WhatIfScenario).where(WhatIfScenario.id == scenario_id)
            )
            scenario = result.scalar_one_or_none()
            if not scenario:
                return

            scenario.status = ScenarioStatus.running
            await db.commit()

            case_result = await db.execute(select(Case).where(Case.id == scenario.case_id))
            case = case_result.scalar_one_or_none()
            if not case:
                scenario.status = ScenarioStatus.failed
                await db.commit()
                return

            # Hydrate the real terminal CaseState from the checkpoint. The
            # legacy path constructed an empty CaseState(case_id, run_id),
            # which is why every what-if used to diff garbage against garbage.
            if not case.latest_run_id:
                logger.error(
                    "what-if scenario %s aborted: case %s has no latest_run_id "
                    "(completed before backfill, or pipeline never finished)",
                    scenario.id,
                    case.id,
                )
                scenario.status = ScenarioStatus.failed
                await db.commit()
                return

            try:
                case_state = await load_case_state(db, case_id=case.id, run_id=case.latest_run_id)
            except (CheckpointSchemaMismatchError, CheckpointCorruptError) as exc:
                logger.error(
                    "what-if scenario %s aborted: checkpoint unreadable for case=%s run=%s: %s",
                    scenario.id,
                    case.id,
                    case.latest_run_id,
                    exc,
                )
                scenario.status = ScenarioStatus.failed
                await db.commit()
                return

            if case_state is None:
                logger.error(
                    "what-if scenario %s aborted: no checkpoint row for case=%s run=%s",
                    scenario.id,
                    case.id,
                    case.latest_run_id,
                )
                scenario.status = ScenarioStatus.failed
                await db.commit()
                return

            controller = WhatIfController(GraphPipelineRunner())
            modified_state = await controller.create_scenario(
                case_state,
                scenario.modification_type.value,
                scenario.modification_payload or {},
            )

            diff = generate_diff(case_state, modified_state)

            result_record = WhatIfResult(
                scenario_id=scenario.id,
                original_analysis=diff["original_verdict"],
                modified_analysis=diff["modified_verdict"],
                diff_view=diff,
                analysis_changed=diff["verdict_changed"],
            )
            db.add(result_record)

            scenario.scenario_run_id = modified_state.run_id
            scenario.status = ScenarioStatus.completed
            scenario.completed_at = datetime.now(UTC)
            await db.commit()

        except Exception:
            scenario.status = ScenarioStatus.failed
            await db.commit()
            raise


async def _run_stability_computation(
    stability_id: uuid.UUID,
    *,
    trace_id: str | None = None,  # noqa: ARG001
) -> None:
    """Background task that computes the stability score.

    `trace_id` accepted for parity with `_run_case_pipeline`; threading
    through `WhatIfController` is deferred to Sprint 4 A5.
    """
    from src.db.pipeline_state import (
        CheckpointCorruptError,
        CheckpointSchemaMismatchError,
        load_case_state,
    )
    from src.pipeline.graph.runner import GraphPipelineRunner
    from src.services.database import async_session
    from src.services.whatif_controller.controller import WhatIfController

    async with async_session() as db:
        try:
            result = await db.execute(
                select(StabilityScore).where(StabilityScore.id == stability_id)
            )
            stability = result.scalar_one_or_none()
            if not stability:
                return

            stability.status = StabilityStatus.computing
            await db.commit()

            case_result = await db.execute(select(Case).where(Case.id == stability.case_id))
            case = case_result.scalar_one_or_none()
            if not case or not case.latest_run_id:
                logger.error(
                    "stability %s aborted: case %s missing or has no latest_run_id",
                    stability.id,
                    stability.case_id,
                )
                stability.status = StabilityStatus.failed
                await db.commit()
                return

            try:
                case_state = await load_case_state(db, case_id=case.id, run_id=case.latest_run_id)
            except (CheckpointSchemaMismatchError, CheckpointCorruptError) as exc:
                logger.error(
                    "stability %s aborted: checkpoint unreadable for case=%s run=%s: %s",
                    stability.id,
                    case.id,
                    case.latest_run_id,
                    exc,
                )
                stability.status = StabilityStatus.failed
                await db.commit()
                return

            if case_state is None:
                logger.error(
                    "stability %s aborted: no checkpoint for case=%s run=%s",
                    stability.id,
                    case.id,
                    case.latest_run_id,
                )
                stability.status = StabilityStatus.failed
                await db.commit()
                return

            # Anchor the stability row at the real terminal run_id.
            stability.run_id = case.latest_run_id

            controller = WhatIfController(GraphPipelineRunner())
            score_result = await controller.compute_stability_score(
                case_state, n=stability.perturbation_count
            )

            stability.score = score_result["score"]
            stability.classification = StabilityClassification(score_result["classification"])
            stability.perturbations_held = score_result["perturbations_held"]
            stability.perturbation_details = {"details": score_result["details"]}
            stability.status = StabilityStatus.completed
            stability.completed_at = datetime.now(UTC)
            await db.commit()

        except Exception:
            stability.status = StabilityStatus.failed
            await db.commit()
            raise


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.post(
    "/{case_id}/what-if",
    response_model=WhatIfResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="submit_whatif_scenario",
    summary="Submit a what-if scenario",
    description="Submit a hypothetical modification for a case to test analysis stability. "
    "The scenario runs asynchronously in the background. "
    "Case must be in `ready_for_review` status.",
    responses={
        400: {"model": ErrorResponse, "description": "Case not in valid status"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions (judge only)"},
        404: {"model": ErrorResponse, "description": "Case not found"},
        422: {"model": ValidationErrorResponse, "description": "Validation error"},
    },
)
async def submit_whatif_scenario(
    case_id: uuid.UUID,
    body: WhatIfRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> WhatIfResponse:
    result = await db.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found",
        )

    if case.status != CaseStatus.ready_for_review:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Case must be in 'ready_for_review' status. Current status: '{case.status.value}'"
            ),
        )

    if not case.latest_run_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Case has no terminal pipeline run on record. What-if requires "
                "a completed run to contest; re-run the pipeline first."
            ),
        )

    scenario = WhatIfScenario(
        case_id=case_id,
        original_run_id=case.latest_run_id,
        scenario_run_id=str(uuid.uuid4()),
        modification_type=body.modification_type,
        modification_description=body.description,
        modification_payload=body.modification_payload,
        status=ScenarioStatus.pending,
        created_by=current_user.id,
    )
    db.add(scenario)
    await db.flush()
    scenario_id = scenario.id

    # Outbox INSERT shares the scenario-insert transaction so a post-commit
    # crash cannot leave a `pending` scenario without a pending job.
    from src.models.pipeline_job import PipelineJobType
    from src.workers.outbox import enqueue_outbox_job

    await enqueue_outbox_job(
        db,
        case_id=case_id,
        job_type=PipelineJobType.whatif_scenario,
        target_id=scenario_id,
    )
    await db.commit()

    return WhatIfResponse(
        scenario_id=scenario_id,
        status=ScenarioStatus.pending,
        message="What-if scenario submitted. Poll the GET endpoint for results.",
    )


@router.get(
    "/{case_id}/what-if/{scenario_id}",
    response_model=WhatIfResultResponse,
    operation_id="get_whatif_result",
    summary="Get what-if scenario result",
    description="Retrieve the result of a what-if scenario including the verdict diff view.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions (judge only)"},
        404: {"model": ErrorResponse, "description": "Scenario not found"},
    },
)
async def get_whatif_result(
    case_id: uuid.UUID,
    scenario_id: uuid.UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> WhatIfResultResponse:
    result = await db.execute(
        select(WhatIfScenario)
        .options(selectinload(WhatIfScenario.result))
        .where(
            WhatIfScenario.id == scenario_id,
            WhatIfScenario.case_id == case_id,
        )
    )
    scenario = result.scalar_one_or_none()
    if not scenario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scenario not found",
        )

    response = WhatIfResultResponse(
        id=scenario.id,
        case_id=scenario.case_id,
        original_run_id=scenario.original_run_id,
        scenario_run_id=scenario.scenario_run_id,
        modification_type=scenario.modification_type,
        modification_description=scenario.modification_description,
        modification_payload=scenario.modification_payload,
        status=scenario.status,
        created_at=scenario.created_at,
        completed_at=scenario.completed_at,
    )

    if scenario.result:
        response.original_verdict = scenario.result.original_analysis
        response.modified_verdict = scenario.result.modified_analysis
        response.diff_view = scenario.result.diff_view
        response.verdict_changed = scenario.result.analysis_changed

    return response


@router.post(
    "/{case_id}/stability",
    response_model=StabilityResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="trigger_stability_score",
    summary="Trigger stability score computation",
    description="Start computing a stability score for a case by running multiple "
    "perturbations and measuring how often the verdict holds.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions (judge only)"},
        404: {"model": ErrorResponse, "description": "Case not found"},
        422: {"model": ValidationErrorResponse, "description": "Validation error"},
    },
)
async def trigger_stability_score(
    case_id: uuid.UUID,
    body: StabilityRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> StabilityResponse:
    result = await db.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found",
        )

    stability = StabilityScore(
        case_id=case_id,
        run_id=str(uuid.uuid4()),
        score=0,
        classification=StabilityClassification.stable,
        perturbation_count=body.perturbation_count,
        perturbations_held=0,
        status=StabilityStatus.pending,
    )
    db.add(stability)
    await db.flush()
    stability_id = stability.id

    # Outbox INSERT shares the stability-insert transaction so a post-commit
    # crash cannot leave a `pending` stability row without a pending job.
    from src.models.pipeline_job import PipelineJobType
    from src.workers.outbox import enqueue_outbox_job

    await enqueue_outbox_job(
        db,
        case_id=case_id,
        job_type=PipelineJobType.stability_computation,
        target_id=stability_id,
    )
    await db.commit()

    return StabilityResponse(
        stability_id=stability_id,
        status=StabilityStatus.pending,
        message="Stability score computation started. Poll the GET endpoint for results.",
    )


@router.get(
    "/{case_id}/stability",
    response_model=StabilityResultResponse,
    operation_id="get_stability_score",
    summary="Get latest stability score",
    description="Retrieve the most recent stability score for a case.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions (judge only)"},
        404: {"model": ErrorResponse, "description": "No stability score found"},
    },
)
async def get_stability_score(
    case_id: uuid.UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> StabilityResultResponse:
    result = await db.execute(
        select(StabilityScore)
        .where(StabilityScore.case_id == case_id)
        .order_by(StabilityScore.created_at.desc())
        .limit(1)
    )
    stability = result.scalar_one_or_none()
    if not stability:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No stability score found for this case",
        )

    return StabilityResultResponse(
        id=stability.id,
        case_id=stability.case_id,
        run_id=stability.run_id,
        score=stability.score,
        classification=stability.classification,
        perturbation_count=stability.perturbation_count,
        perturbations_held=stability.perturbations_held,
        perturbation_details=stability.perturbation_details,
        status=stability.status,
        created_at=stability.created_at,
        completed_at=stability.completed_at,
    )
