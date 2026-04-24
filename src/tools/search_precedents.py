"""Live judiciary precedent search tool for VerdictCouncil.

Queries the PAIR Search API (search.pair.gov.sg) for Singapore higher
court case law. Results are cached in Redis with configurable TTL and
rate-limited to 2 requests per second via a Redis-based distributed limiter.
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
import redis.asyncio as redis

from src.shared.circuit_breaker import CircuitState, get_pair_search_breaker
from src.shared.config import settings
from src.shared.retry import retry_with_backoff
from src.tools.exceptions import DegradableToolError
from src.tools.vector_store_fallback import VectorStoreError, vector_store_search

logger = logging.getLogger(__name__)


class PrecedentSearchError(DegradableToolError):
    """Raised when precedent search encounters an unrecoverable error."""


@dataclass
class SearchResult:
    """Result of a precedent search with source metadata."""

    precedents: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(
        default_factory=lambda: {
            "source_failed": False,
            "fallback_used": False,
            "pair_status": "ok",
        }
    )


def _cache_key(query: str, domain: str, vector_store_id: str | None, max_results: int) -> str:
    """Generate a deterministic cache key for a search query.

    vector_store_id is included to avoid cross-domain cache collisions (H6).
    """
    key_data = json.dumps(
        {
            "query": query,
            "domain": domain,
            "vector_store_id": vector_store_id,
            "max_results": max_results,
        },
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
    """Query PAIR Search API for Singapore case law."""
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


async def _search_precedents_impl(
    query: str,
    domain: str = "small_claims",
    max_results: int = 5,
    vector_store_id: str | None = None,
) -> SearchResult:
    """Internal implementation that returns SearchResult with metadata."""
    cache_k = _cache_key(query, domain, vector_store_id, max_results)
    metadata: dict[str, Any] = {
        "source_failed": False,
        "fallback_used": False,
        "pair_status": "ok",
    }

    # Try cache first
    try:
        r = await _get_redis_client()
        cached = await r.get(cache_k)
        if cached:
            logger.info("Precedent cache hit for query: %s", query[:80])
            cached_payload = json.loads(cached)
            if isinstance(cached_payload, dict) and "precedents" in cached_payload:
                return SearchResult(
                    precedents=cached_payload["precedents"],
                    metadata=cached_payload.get("metadata", metadata),
                )
            # Legacy cache format (plain list) — treat as clean results
            return SearchResult(precedents=cached_payload, metadata=metadata)
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
        metadata["pair_status"] = "circuit_open"
        try:
            results = await vector_store_search(
                query,
                domain,
                max_results,
                vector_store_id=vector_store_id,
                allow_global_fallback=True,
            )
        except VectorStoreError as exc:
            logger.warning("Vector store fallback also failed: %s", exc)
            metadata["source_failed"] = True
        used_fallback = True
        # Circuit open + vector store returned empty = all sources exhausted
        if not results and not metadata["source_failed"]:
            metadata["source_failed"] = True
    else:
        # CLOSED or HALF_OPEN — attempt PAIR API
        try:
            if r is None:
                r = await _get_redis_client()
            results = await _call_pair_api(query, domain, max_results, r)
            await pair_breaker.record_success()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            new_state = await pair_breaker.record_failure()
            metadata["pair_status"] = f"failed ({new_state.value})"
            logger.warning(
                "PAIR Search API unreachable for query '%s': %s (circuit: %s). Falling back to vector store.",  # noqa: E501
                query[:80],
                exc,
                new_state.value,
            )
            try:
                results = await vector_store_search(
                    query,
                    domain,
                    max_results,
                    vector_store_id=vector_store_id,
                    allow_global_fallback=True,
                )
            except VectorStoreError as vs_exc:
                logger.warning("Vector store fallback also failed: %s", vs_exc)
                metadata["source_failed"] = True
            used_fallback = True

    metadata["fallback_used"] = used_fallback

    # Tag fallback results
    if used_fallback:
        for result in results:
            result["fallback_used"] = True

    # Sort by similarity descending
    results.sort(key=lambda x: x.get("similarity_score", 0), reverse=True)

    # Cache results with metadata (skip caching total failures to avoid extending outages)
    if not metadata["source_failed"]:
        try:
            if r is None:
                r = await _get_redis_client()
            cache_payload = {"precedents": results, "metadata": metadata}
            await r.setex(
                cache_k,
                settings.precedent_cache_ttl_seconds,
                json.dumps(cache_payload),
            )
        except redis.RedisError:
            logger.warning("Failed to write precedent results to Redis cache")

    logger.info(
        "Precedent search returned %d results (fallback=%s, source_failed=%s) for query: %s",
        len(results),
        used_fallback,
        metadata["source_failed"],
        query[:80],
    )

    return SearchResult(precedents=results, metadata=metadata)


async def search_precedents(
    query: str,
    domain: str = "small_claims",
    max_results: int = 5,
    vector_store_id: str | None = None,
) -> list[dict]:
    """Search PAIR API for precedent cases.

    Backward-compatible public API that returns only the precedent list.
    Used by the SAM tool wrapper and direct callers.
    """
    result = await _search_precedents_impl(query, domain, max_results, vector_store_id)
    return result.precedents


async def search_precedents_with_meta(
    query: str,
    domain: str = "small_claims",
    max_results: int = 5,
    vector_store_id: str | None = None,
) -> SearchResult:
    """Search PAIR API for precedent cases with source metadata.

    Returns a SearchResult containing both the precedent list and
    metadata about source availability. Used by the pipeline runner
    to populate CaseState.precedent_source_metadata.
    """
    return await _search_precedents_impl(query, domain, max_results, vector_store_id)
