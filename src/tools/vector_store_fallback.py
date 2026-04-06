"""Fallback precedent search using OpenAI vector store.

Used when the PAIR Search API circuit breaker is open.
Queries a curated vector store of Singapore higher court decisions
via the OpenAI Responses API with file_search tool.
"""

import logging

from openai import AsyncOpenAI

from src.shared.config import settings

logger = logging.getLogger(__name__)


class VectorStoreError(Exception):
    """Raised when vector store search encounters an unrecoverable error."""


async def vector_store_search(
    query: str,
    domain: str = "small_claims",
    max_results: int = 5,
) -> list[dict]:
    """Query OpenAI vector store for precedent cases.

    Returns results in the same format as PAIR API search results,
    tagged with source: "vector_store_fallback".

    Returns empty list if vector store ID is not configured or
    if the API call fails.
    """
    if not settings.openai_vector_store_id:
        logger.warning("OPENAI_VECTOR_STORE_ID not configured; cannot use vector store fallback")
        raise VectorStoreError("Vector store not configured")

    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)

        response = await client.responses.create(
            model=settings.openai_model_lightweight,
            input=f"Find Singapore court precedents relevant to: {query} (domain: {domain})",
            tools=[
                {
                    "type": "file_search",
                    "vector_store_ids": [settings.openai_vector_store_id],
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
            "Vector store fallback returned %d results for query: %s",
            len(results),
            query[:80],
        )
        return results

    except Exception as exc:
        logger.warning("Vector store fallback failed: %s", exc)
        raise VectorStoreError(str(exc)) from exc
