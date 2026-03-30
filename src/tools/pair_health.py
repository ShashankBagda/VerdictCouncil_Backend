"""PAIR API health probe for circuit breaker maintenance."""

import logging

import httpx

from src.shared.circuit_breaker import get_pair_search_breaker
from src.shared.config import settings

logger = logging.getLogger(__name__)


async def check_pair_health() -> dict:
    """Probe PAIR API with a lightweight query.

    Updates circuit breaker state based on probe result.
    Returns health status dict.
    """
    pair_breaker = get_pair_search_breaker()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                settings.pair_api_url,
                json={
                    "id": "",
                    "hits": 1,
                    "query": "contract breach",
                    "offset": 0,
                    "filters": {
                        "hansardFilters": {},
                        "caseJudgementFilters": {},
                        "legislationFilters": {},
                    },
                    "sources": ["judiciary"],
                    "isLoggingEnabled": False,
                },
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            await pair_breaker.record_success()
            return {"status": "healthy", "response_code": resp.status_code}
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        await pair_breaker.record_failure()
        logger.warning("PAIR health check failed: %s", exc)
        return {"status": "unhealthy", "error": str(exc)}
