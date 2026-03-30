"""Health check endpoints."""

from fastapi import APIRouter

from src.shared.circuit_breaker import CircuitBreaker
from src.tools.pair_health import check_pair_health

router = APIRouter()

_pair_breaker = CircuitBreaker(service_name="pair_search")


@router.get("/pair")
async def pair_health():
    """Get PAIR API circuit breaker status."""
    return await _pair_breaker.get_status()


@router.post("/pair/probe")
async def pair_probe():
    """Actively probe PAIR API and update circuit breaker."""
    return await check_pair_health()
