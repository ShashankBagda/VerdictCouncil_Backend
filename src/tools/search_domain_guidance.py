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

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def search_domain_guidance(
    query: str,
    vector_store_id: str,
    max_results: int = 25,
) -> list[dict]:
    """Query the domain's curated knowledge base.

    Args:
        query: Semantic search query.
        vector_store_id: REQUIRED. The domain's OpenAI vector store ID.
            Injected by the runner from CaseState.domain_vector_store_id.
            Agents must not choose this value themselves.
        max_results: Maximum number of results to return. Default is
            generous (25, OpenAI hard cap 50) — bias toward recall, since
            missing a controlling statute is worse than reading a few
            extra chunks of practice directions.

    Returns:
        List of result dicts with citation, content, and score fields.

    Raises:
        DomainGuidanceUnavailable: If vector_store_id is falsy.
            This is a CriticalToolFailure — it halts the gate, not degrade it.
    """
    if not vector_store_id:
        raise DomainGuidanceUnavailable("Domain has no provisioned vector store")

    # OpenAI file_search hard-caps at 50 per call.
    max_results = max(1, min(int(max_results or 25), 50))

    try:
        client = _get_client()

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
            # Without this include the Responses API runs file_search but
            # returns `file_search_call.results = None` — the LLM sees the
            # chunks but the caller cannot.
            include=["file_search_call.results"],
        )

        results = []
        for item in response.output:
            if item.type == "file_search_call" and item.results:
                for result in item.results[:max_results]:
                    results.append(
                        {
                            "citation": result.filename or "Unknown",
                            # Up from 1000 — clip only egregiously long
                            # chunks so we don't blow the prompt budget on
                            # a single retrieval, while still giving the
                            # agent enough context to reason from.
                            "content": (result.text or "")[:4000],
                            "score": result.score if result.score else 0,
                            "source": "domain_guidance",
                            "file_id": getattr(result, "file_id", "") or "",
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
