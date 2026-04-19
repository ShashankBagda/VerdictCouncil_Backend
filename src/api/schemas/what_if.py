"""What-If scenario and stability score schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.models.what_if import (
    ModificationType,
    ScenarioStatus,
    StabilityClassification,
    StabilityStatus,
)


class WhatIfRequest(BaseModel):
    """Submit a what-if modification for a case."""

    modification_type: ModificationType = Field(
        ..., description="Type of modification to apply", examples=["remove_evidence"]
    )
    modification_payload: dict[str, Any] = Field(
        ..., description="Modification parameters (structure depends on modification_type)"
    )
    description: str | None = Field(
        None, description="Human-readable description of the modification"
    )


class WhatIfResponse(BaseModel):
    """Acknowledgment of submitted what-if scenario."""

    scenario_id: uuid.UUID = Field(..., description="ID of the created scenario")
    status: ScenarioStatus = Field(..., description="Current scenario status")
    message: str = Field(..., description="Status message")


class WhatIfResultResponse(BaseModel):
    """Full what-if scenario result with verdict diff."""

    id: uuid.UUID = Field(..., description="Scenario ID")
    case_id: uuid.UUID = Field(..., description="Original case ID")
    original_run_id: str = Field(..., description="Run ID of the original pipeline execution")
    scenario_run_id: str = Field(..., description="Run ID of the modified pipeline execution")
    modification_type: ModificationType = Field(..., description="Type of modification applied")
    modification_description: str | None = Field(None, description="Modification description")
    modification_payload: dict[str, Any] | None = Field(None, description="Modification parameters")
    status: ScenarioStatus = Field(..., description="Scenario processing status")
    created_at: datetime = Field(..., description="Creation timestamp")
    completed_at: datetime | None = Field(None, description="Completion timestamp")
    original_verdict: dict[str, Any] | None = Field(None, description="Original verdict data")
    modified_verdict: dict[str, Any] | None = Field(None, description="Modified verdict data")
    diff_view: dict[str, Any] | None = Field(None, description="Side-by-side diff of verdicts")
    verdict_changed: bool | None = Field(None, description="Whether the verdict changed")

    model_config = {"from_attributes": True}


class StabilityRequest(BaseModel):
    """Request stability score computation."""

    perturbation_count: int = Field(
        5, ge=1, le=20, description="Number of perturbations to test", examples=[5]
    )


class StabilityResponse(BaseModel):
    """Acknowledgment of stability score computation."""

    stability_id: uuid.UUID = Field(..., description="ID of the stability computation")
    status: StabilityStatus = Field(..., description="Computation status")
    message: str = Field(..., description="Status message")


class StabilityResultResponse(BaseModel):
    """Stability score result."""

    id: uuid.UUID = Field(..., description="Stability score ID")
    case_id: uuid.UUID = Field(..., description="Case ID")
    run_id: str = Field(..., description="Pipeline run ID")
    score: int = Field(..., description="Stability score (0-100)", examples=[85])
    classification: StabilityClassification = Field(
        ..., description="Stability classification", examples=["stable"]
    )
    perturbation_count: int = Field(..., description="Number of perturbations tested")
    perturbations_held: int = Field(
        ..., description="Number of perturbations that held the verdict"
    )
    perturbation_details: dict[str, Any] | None = Field(
        None, description="Detailed perturbation results"
    )
    status: StabilityStatus = Field(..., description="Computation status")
    created_at: datetime = Field(..., description="Creation timestamp")
    completed_at: datetime | None = Field(None, description="Completion timestamp")

    model_config = {"from_attributes": True}
