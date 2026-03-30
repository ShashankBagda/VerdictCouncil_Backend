"""Health check endpoints."""

from fastapi import APIRouter

from src.shared.circuit_breaker import get_pair_search_breaker
from src.tools.pair_health import check_pair_health

router = APIRouter()


@router.get("/pair")
async def pair_health():
    """Get PAIR API circuit breaker status."""
    return await get_pair_search_breaker().get_status()


@router.post("/pair/probe")
async def pair_probe():
    """Actively probe PAIR API and update circuit breaker."""
    return await check_pair_health()
