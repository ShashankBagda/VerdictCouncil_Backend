"""Health check response schemas."""

from pydantic import BaseModel, Field


class PairHealthResponse(BaseModel):
    """PAIR API circuit breaker status.

    Returns different optional fields depending on whether Redis is available.
    Normal: service, state, failure_count, failure_threshold, recovery_timeout_seconds, opened_at.
    Error path: service, state, failure_count, error.
    """

    service: str = Field(..., description="Service name", examples=["pair_search"])
    state: str = Field(..., description="Circuit breaker state", examples=["closed"])
    failure_count: int = Field(..., description="Current failure count (-1 if Redis unavailable)")
    failure_threshold: int | None = Field(
        None, description="Failures before circuit opens", examples=[3]
    )
    recovery_timeout_seconds: int | None = Field(
        None, description="Seconds before recovery attempt", examples=[60]
    )
    opened_at: float | None = Field(None, description="Unix timestamp when circuit opened")
    error: str | None = Field(None, description="Error message if Redis is unavailable")


class PairProbeResponse(BaseModel):
    """PAIR API active probe result.

    Healthy: {status, response_code}. Unhealthy: {status, error}.
    """

    status: str = Field(..., description="Probe result", examples=["healthy"])
    response_code: int | None = Field(None, description="HTTP status from PAIR API", examples=[200])
    error: str | None = Field(None, description="Error message if probe failed")
