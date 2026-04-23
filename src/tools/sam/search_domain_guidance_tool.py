"""SAM DynamicTool wrapper for the search_domain_guidance tool.

Exposes search_domain_guidance as a SAM-compatible tool for the legal-knowledge agent.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SEARCH_DOMAIN_GUIDANCE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "query": {
            "type": "STRING",
            "description": "Semantic query for statutes, practice directions, or bench books",
        },
        "vector_store_id": {
            "type": "STRING",
            "description": "Domain vector store ID (injected by runner; do not choose this yourself)",
        },
        "max_results": {
            "type": "INTEGER",
            "description": "Maximum number of guidance results to return",
        },
    },
    "required": ["query", "vector_store_id"],
}


class SearchDomainGuidanceTool:
    """SAM-compatible tool that delegates to search_domain_guidance.

    Implements the DynamicTool protocol expected by solace-agent-mesh.
    """

    @property
    def tool_name(self) -> str:
        return "search_domain_guidance"

    @property
    def tool_description(self) -> str:
        return (
            "Query the domain's curated knowledge base for statutes, practice directions, "
            "bench books, and procedural rules. Domain-specific — always hits the right corpus."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return SEARCH_DOMAIN_GUIDANCE_SCHEMA

    async def init(self, component: Any = None, tool_config: Any = None) -> None:
        logger.debug("SearchDomainGuidanceTool initialized")

    async def cleanup(self, component: Any = None, tool_config: Any = None) -> None:
        logger.debug("SearchDomainGuidanceTool cleaned up")

    async def _run_async_impl(
        self,
        *,
        args: dict[str, Any],
        tool_context: Any = None,
    ) -> Any:
        """Execute search_domain_guidance with the given arguments.

        Args-first / state-fallback pattern (D11):
        1. Use vector_store_id from args if provided (in-process runner path).
        2. Fall back to tool_context.state["domain_vector_store_id"] (mesh path).
        3. If neither is present, raise DomainGuidanceUnavailable.
        """
        from src.tools.exceptions import DomainGuidanceUnavailable
        from src.tools.search_domain_guidance import search_domain_guidance

        # Args-first / state-fallback
        if "vector_store_id" not in args or not args["vector_store_id"]:
            state = getattr(tool_context, "state", None)
            if isinstance(state, dict):
                vsid = state.get("domain_vector_store_id")
                if vsid:
                    args = {**args, "vector_store_id": vsid}

        if not args.get("vector_store_id"):
            raise DomainGuidanceUnavailable("No domain_vector_store_id available for tool")

        return await search_domain_guidance(**args)
