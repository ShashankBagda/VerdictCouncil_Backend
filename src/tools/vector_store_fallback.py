"""Precedent search using OpenAI vector store.

Used when the PAIR Search API circuit breaker is open.
Queries a curated vector store of Singapore higher court decisions
via the OpenAI Responses API with file_search tool.
"""

import logging

from openai import AsyncOpenAI

from src.shared.config import settings
from src.tools.exceptions import DegradableToolError

logger = logging.getLogger(__name__)


class VectorStoreError(DegradableToolError):
    """Raised when vector store search encounters an unrecoverable error."""


async def vector_store_search(
    query: str,
    domain: str = "small_claims",
    max_results: int = 5,
    *,
    vector_store_id: str | None = None,
    allow_global_fallback: bool = False,
) -> list[dict]:
    """Query an OpenAI vector store for precedent cases.

    Fail-closed semantics:
    - vector_store_id provided → use it.
    - vector_store_id=None + allow_global_fallback=True + global id configured → use global, log WARN.
    - vector_store_id=None + allow_global_fallback=False → raise VectorStoreError immediately.
    - All other None combinations → raise VectorStoreError.

    Returns results in the same format as PAIR API search results,
    tagged with source: "vector_store_fallback".
    """
    effective_id = vector_store_id

    if effective_id is None:
        if allow_global_fallback and settings.openai_vector_store_id:
            logger.warning(
                "Domain not provisioned; falling back to global vector store. "
                "query=%s domain=%s",
                query[:80],
                domain,
            )
            effective_id = settings.openai_vector_store_id
        else:
            if not settings.openai_vector_store_id:
                raise VectorStoreError("Vector store not configured")
            raise VectorStoreError(
                "No domain vector store id provided and allow_global_fallback is False"
            )

    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)

        response = await client.responses.create(
            model=settings.openai_model_lightweight,
            input=f"Find Singapore court precedents relevant to: {query} (domain: {domain})",
            tools=[
                {
                    "type": "file_search",
                    "vector_store_ids": [effective_id],
                    "max_num_results": max_results,
                }
            ],
        )

        results = []
        for item in response.output:
            if item.type == "file_search_call" and item.results:
                for result in item.results[:max_results]:
                    results.append(
                        {
                            "citation": result.filename or "Unknown",
                            "court": "",
                            "outcome": "",
                            "reasoning_summary": (result.text or "")[:500],
                            "similarity_score": result.score if result.score else 0,
                            "url": "",
                            "source": "vector_store_fallback",
                        }
                    )

        logger.info(
            "Vector store search returned %d results for query: %s",
            len(results),
            query[:80],
        )
        return results

    except VectorStoreError:
        raise
    except Exception as exc:
        logger.warning("Vector store search failed: %s", exc)
        raise VectorStoreError(str(exc)) from exc
