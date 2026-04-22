"""Dashboard statistics schema."""

from pydantic import BaseModel, Field


class DashboardStats(BaseModel):
    """Aggregate case statistics and system health."""

    total_cases: int = Field(..., description="Total number of cases in the system", examples=[42])
    by_status: dict[str, int] = Field(
        ...,
        description="Case count grouped by status",
        examples=[{"pending": 10, "ready_for_review": 5, "closed": 3}],
    )
    by_domain: dict[str, int] = Field(
        ...,
        description="Case count grouped by domain",
        examples=[{"small_claims": 30, "traffic_violation": 12}],
    )
    escalation_rate_percent: float = Field(
        ..., description="Percentage of cases currently escalated"
    )
    average_processing_time_seconds: float | None = Field(
        None, description="Average pipeline processing time when available"
    )
    recent_cases: list[dict] = Field(..., description="Most recent cases (last 10)")
    pair_api_status: dict | None = Field(None, description="PAIR API circuit breaker status")
