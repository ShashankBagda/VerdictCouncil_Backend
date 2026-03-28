"""What-If scenario API endpoints for Contestable Judgment Mode."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.api.deps import DBSession, require_role
from src.models.case import Case, CaseStatus
from src.models.user import User, UserRole
from src.models.what_if import (
    ModificationType,
    ScenarioStatus,
    StabilityClassification,
    StabilityScore,
    StabilityStatus,
    WhatIfScenario,
    WhatIfVerdict,
)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class WhatIfRequest(BaseModel):
    modification_type: ModificationType
    modification_payload: dict[str, Any]
    description: str | None = None


class WhatIfResponse(BaseModel):
    scenario_id: uuid.UUID
    status: ScenarioStatus
    message: str


class WhatIfResultResponse(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    original_run_id: str
    scenario_run_id: str
    modification_type: ModificationType
    modification_description: str | None
    modification_payload: dict[str, Any] | None
    status: ScenarioStatus
    created_at: datetime
    completed_at: datetime | None
    original_verdict: dict[str, Any] | None = None
    modified_verdict: dict[str, Any] | None = None
    diff_view: dict[str, Any] | None = None
    verdict_changed: bool | None = None

    model_config = {"from_attributes": True}


class StabilityRequest(BaseModel):
    perturbation_count: int = 5


class StabilityResponse(BaseModel):
    stability_id: uuid.UUID
    status: StabilityStatus
    message: str


class StabilityResultResponse(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    run_id: str
    score: int
    classification: StabilityClassification
    perturbation_count: int
    perturbations_held: int
    perturbation_details: dict[str, Any] | None
    status: StabilityStatus
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Background task helpers
# --------------------------------------------------------------------------- #


async def _run_whatif_scenario(scenario_id: uuid.UUID) -> None:
    """Background task that executes the what-if scenario.

    Imports are deferred to avoid circular dependencies and to create
    a fresh database session for the background task.
    """
    from src.pipeline.runner import PipelineRunner
    from src.services.database import async_session
    from src.services.whatif_controller.controller import WhatIfController
    from src.services.whatif_controller.diff_engine import generate_diff
    from src.shared.case_state import CaseState

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

            # Build a CaseState from the case data
            # In a full implementation this would load from a state store;
            # here we construct a minimal state for the pipeline runner.
            case_result = await db.execute(select(Case).where(Case.id == scenario.case_id))
            case = case_result.scalar_one_or_none()
            if not case:
                scenario.status = ScenarioStatus.failed
                await db.commit()
                return

            case_state = CaseState(
                case_id=str(case.id),
                run_id=scenario.original_run_id,
            )

            controller = WhatIfController(PipelineRunner())
            modified_state = await controller.create_scenario(
                case_state,
                scenario.modification_type.value,
                scenario.modification_payload or {},
            )

            diff = generate_diff(case_state, modified_state)

            verdict_record = WhatIfVerdict(
                scenario_id=scenario.id,
                original_verdict=diff["original_verdict"],
                modified_verdict=diff["modified_verdict"],
                diff_view=diff,
                verdict_changed=diff["verdict_changed"],
            )
            db.add(verdict_record)

            scenario.scenario_run_id = modified_state.run_id
            scenario.status = ScenarioStatus.completed
            scenario.completed_at = datetime.now(UTC)
            await db.commit()

        except Exception:
            scenario.status = ScenarioStatus.failed
            await db.commit()
            raise


async def _run_stability_computation(stability_id: uuid.UUID) -> None:
    """Background task that computes the stability score."""
    from src.pipeline.runner import PipelineRunner
    from src.services.database import async_session
    from src.services.whatif_controller.controller import WhatIfController
    from src.shared.case_state import CaseState

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

            case_state = CaseState(
                case_id=str(stability.case_id),
                run_id=stability.run_id,
            )

            controller = WhatIfController(PipelineRunner())
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
)
async def submit_whatif_scenario(
    case_id: uuid.UUID,
    body: WhatIfRequest,
    background_tasks: BackgroundTasks,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> WhatIfResponse:
    """Submit a what-if modification for a case.

    Requires judge role. Case must be in ready_for_review or decided status.
    The scenario runs asynchronously in the background.
    """
    # Verify case exists and is in a valid status
    result = await db.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found",
        )

    if case.status not in (CaseStatus.ready_for_review, CaseStatus.decided):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Case must be in 'ready_for_review' or 'decided' status. "
                f"Current status: '{case.status.value}'"
            ),
        )

    # Create scenario record
    scenario = WhatIfScenario(
        case_id=case_id,
        original_run_id=str(uuid.uuid4()),  # Would come from state store in production
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
    background_tasks.add_task(_run_whatif_scenario, scenario_id)

    return WhatIfResponse(
        scenario_id=scenario_id,
        status=ScenarioStatus.pending,
        message="What-if scenario submitted. Poll the GET endpoint for results.",
    )


@router.get("/{case_id}/what-if/{scenario_id}", response_model=WhatIfResultResponse)
async def get_whatif_result(
    case_id: uuid.UUID,
    scenario_id: uuid.UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> WhatIfResultResponse:
    """Get a what-if scenario result with diff view."""
    result = await db.execute(
        select(WhatIfScenario)
        .options(selectinload(WhatIfScenario.verdict))
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

    if scenario.verdict:
        response.original_verdict = scenario.verdict.original_verdict
        response.modified_verdict = scenario.verdict.modified_verdict
        response.diff_view = scenario.verdict.diff_view
        response.verdict_changed = scenario.verdict.verdict_changed

    return response


@router.post(
    "/{case_id}/stability",
    response_model=StabilityResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_stability_score(
    case_id: uuid.UUID,
    body: StabilityRequest,
    background_tasks: BackgroundTasks,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> StabilityResponse:
    """Trigger stability score computation for a case."""
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
    background_tasks.add_task(_run_stability_computation, stability_id)

    return StabilityResponse(
        stability_id=stability_id,
        status=StabilityStatus.pending,
        message="Stability score computation started. Poll the GET endpoint for results.",
    )


@router.get("/{case_id}/stability", response_model=StabilityResultResponse)
async def get_stability_score(
    case_id: uuid.UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> StabilityResultResponse:
    """Get the latest stability score for a case."""
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
