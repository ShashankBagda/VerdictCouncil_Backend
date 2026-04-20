"""Dashboard statistics schema."""

from pydantic import BaseModel, Field


class DashboardStats(BaseModel):
    """Aggregate case statistics and system health."""

    total_cases: int = Field(..., description="Total number of cases in the system", examples=[42])
    by_status: dict[str, int] = Field(
        ..., description="Case count grouped by status", examples=[{"pending": 10, "decided": 25}]
    )
    by_domain: dict[str, int] = Field(
        ...,
        description="Case count grouped by domain",
        examples=[{"small_claims": 30, "traffic_violation": 12}],
    )
    recent_cases: list[dict] = Field(..., description="Most recent cases (last 10)")
    pair_api_status: dict | None = Field(None, description="PAIR API circuit breaker status")
