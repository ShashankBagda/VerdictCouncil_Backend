"""Per-domain guidance retrieval tool.

Queries the domain's curated vector store for statutes, practice directions,
bench books, and procedural rules. This is the primary source for domain-scoped
RAG — unlike search_precedents (PAIR-primary), this tool hits the domain store
on every invocation, not as a fallback.

Domain guidance content is admin-curated; treat as trusted-but-verify.
"""

import logging

from openai import AsyncOpenAI

from src.shared.config import settings
from src.tools.exceptions import DomainGuidanceUnavailable

logger = logging.getLogger(__name__)


async def search_domain_guidance(
    query: str,
    vector_store_id: str,
    max_results: int = 5,
) -> list[dict]:
    """Query the domain's curated knowledge base.

    Args:
        query: Semantic search query.
        vector_store_id: REQUIRED. The domain's OpenAI vector store ID.
            Injected by the runner from CaseState.domain_vector_store_id.
            Agents must not choose this value themselves.
        max_results: Maximum number of results to return.

    Returns:
        List of result dicts with citation, content, and score fields.

    Raises:
        DomainGuidanceUnavailable: If vector_store_id is falsy.
            This is a CriticalToolFailure — it halts the gate, not degrade it.
    """
    if not vector_store_id:
        raise DomainGuidanceUnavailable("Domain has no provisioned vector store")

    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)

        response = await client.responses.create(
            model=settings.openai_model_lightweight,
            input=f"Find relevant statutes, practice directions, or procedural rules for: {query}",
            tools=[
                {
                    "type": "file_search",
                    "vector_store_ids": [vector_store_id],
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
                            "content": (result.text or "")[:1000],
                            "score": result.score if result.score else 0,
                            "source": "domain_guidance",
                        }
                    )

        logger.info(
            "Domain guidance search returned %d results for query: %s (store=%s)",
            len(results),
            query[:80],
            vector_store_id,
        )
        return results

    except DomainGuidanceUnavailable:
        raise
    except Exception as exc:
        logger.error("Domain guidance search failed for store %s: %s", vector_store_id, exc)
        raise DomainGuidanceUnavailable(f"Domain guidance search failed: {exc}") from exc
