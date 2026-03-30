"""Live judiciary precedent search tool for VerdictCouncil.

Queries the PAIR Search API (search.pair.gov.sg) for Singapore higher
court case law. Results are cached in Redis with configurable TTL and
rate-limited to 2 requests per second via a Redis-based distributed limiter.
"""

import asyncio
import hashlib
import json
import logging

import httpx
import redis.asyncio as redis

from src.shared.circuit_breaker import CircuitState, get_pair_search_breaker
from src.shared.config import settings
from src.shared.retry import retry_with_backoff
from src.tools.vector_store_fallback import vector_store_search

logger = logging.getLogger(__name__)


class PrecedentSearchError(Exception):
    """Raised when precedent search encounters an unrecoverable error."""


def _cache_key(query: str, domain: str, max_results: int) -> str:
    """Generate a deterministic cache key for a search query."""
    key_data = json.dumps(
        {"query": query, "domain": domain, "max_results": max_results},
        sort_keys=True,
    )
    return f"vc:precedents:{hashlib.sha256(key_data.encode()).hexdigest()}"


async def _get_redis_client() -> redis.Redis:
    """Create an async Redis client from settings."""
    return redis.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
    )


async def _rate_limit(r: redis.Redis) -> None:
    """Enforce distributed rate limit: max 2 requests per second.

    Uses Redis INCR with a 1-second TTL key. If the count exceeds
    the limit, sleeps until the window resets.
    """
    key = "vc:ratelimit:pair_search"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, 1)
    if count > 2:
        await asyncio.sleep(1.0)


@retry_with_backoff(
    max_retries=2,
    base_delay=1.0,
    retryable_exceptions=(httpx.TransportError, httpx.TimeoutException),
)
async def _call_pair_api(
    query: str,
    domain: str,
    max_results: int,
    r: redis.Redis,
) -> list[dict]:
    """Query PAIR Search API for Singapore case law.

    PAIR (Platform for AI-assisted Research) is a Singapore government
    legal research platform. Its search API provides hybrid retrieval
    (BM25 + semantic embedding) over the full corpus of Singapore
    judiciary decisions on eLitigation.

    Court coverage: SGHC, SGCA, SGHCF, SGHCR, SGHC(I), SGHC(A), SGCA(I).
    Does NOT cover Small Claims Tribunals or lower State Courts, but
    higher court rulings are binding on lower courts.
    """
    await _rate_limit(r)

    payload = {
        "id": "",
        "hits": max_results,
        "query": query,
        "offset": 0,
        "filters": {
            "hansardFilters": {},
            "caseJudgementFilters": {},
            "legislationFilters": {},
        },
        "sources": ["judiciary"],
        "isLoggingEnabled": False,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            settings.pair_api_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for item in data.get("searchResults", [])[:max_results]:
        results.append(
            {
                "citation": item.get("citationNum", ""),
                "court": item.get("court", ""),
                "outcome": "",  # Not available in search results
                "reasoning_summary": item.get("snippet", ""),
                "similarity_score": item.get("matchScore", {}).get("score", 0),
                "url": item.get("url", ""),
                "source": "live_search",
            }
        )

    return results


async def search_precedents(
    query: str,
    domain: str = "small_claims",
    max_results: int = 5,
) -> list[dict]:
    """Search PAIR API for precedent cases.

    Searches search.pair.gov.sg for published judiciary decisions from
    Singapore's higher courts. Results are cached in Redis with a
    configurable TTL (default 24 hours). Rate-limited to 2 req/sec.

    Args:
        query: Semantic search query describing the legal issue or
            fact pattern to find precedents for.
        domain: Legal domain context. One of "small_claims" or
            "traffic". Defaults to "small_claims".
        max_results: Maximum number of precedents to return.
            Defaults to 5.

    Returns:
        List of precedent dicts, each with: citation, court, outcome,
        reasoning_summary, similarity_score, url, source.
        Returns empty list with a logged warning if PAIR API is
        unreachable (does not silently fail).
    """
    cache_k = _cache_key(query, domain, max_results)

    # Try cache first
    try:
        r = await _get_redis_client()
        cached = await r.get(cache_k)
        if cached:
            logger.info("Precedent cache hit for query: %s", query[:80])
            return json.loads(cached)
    except (redis.RedisError, json.JSONDecodeError):
        logger.warning("Redis cache read failed; proceeding with live search")
        r = None

    # Check circuit breaker state (with recovery timeout check)
    pair_breaker = get_pair_search_breaker()
    breaker_state = await pair_breaker.check_recovery()

    results: list[dict] = []
    used_fallback = False

    if breaker_state == CircuitState.OPEN:
        # Circuit is open — skip PAIR, go straight to fallback
        logger.info("Circuit breaker OPEN for pair_search; using vector store fallback")
        results = await vector_store_search(query, domain, max_results)
        used_fallback = True
    else:
        # CLOSED or HALF_OPEN — attempt PAIR API
        try:
            if r is None:
                r = await _get_redis_client()
            results = await _call_pair_api(query, domain, max_results, r)
            await pair_breaker.record_success()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            new_state = await pair_breaker.record_failure()
            logger.warning(
                "PAIR Search API unreachable for query '%s': %s (circuit: %s). "
                "Falling back to vector store.",
                query[:80],
                exc,
                new_state.value,
            )
            results = await vector_store_search(query, domain, max_results)
            used_fallback = True

    # Tag fallback results
    if used_fallback:
        for result in results:
            result["fallback_used"] = True

    # Sort by similarity descending
    results.sort(key=lambda x: x.get("similarity_score", 0), reverse=True)

    # Cache results
    try:
        if r is None:
            r = await _get_redis_client()
        await r.setex(
            cache_k,
            settings.precedent_cache_ttl_seconds,
            json.dumps(results),
        )
    except redis.RedisError:
        logger.warning("Failed to write precedent results to Redis cache")

    logger.info(
        "Precedent search returned %d results (fallback=%s) for query: %s",
        len(results),
        used_fallback,
        query[:80],
    )

    return results
