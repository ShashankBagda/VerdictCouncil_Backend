"""Health check endpoints."""

from fastapi import APIRouter

from src.api.schemas.health import PairHealthResponse, PairProbeResponse
from src.shared.circuit_breaker import get_pair_search_breaker
from src.tools.pair_health import check_pair_health

router = APIRouter()


@router.get(
    "/pair",
    response_model=PairHealthResponse,
    operation_id="pair_health_status",
    summary="Get PAIR API circuit breaker status",
    description="Returns the current state of the PAIR API circuit breaker "
    "including failure count, threshold, and recovery timeout.",
    openapi_extra={"security": []},
)
async def pair_health():
    return await get_pair_search_breaker().get_status()


@router.post(
    "/pair/probe",
    response_model=PairProbeResponse,
    operation_id="pair_health_probe",
    summary="Actively probe PAIR API health",
    description="Send a lightweight query to the PAIR API and update the circuit "
    "breaker state based on the result.",
    openapi_extra={"security": []},
)
async def pair_probe():
    return await check_pair_health()
