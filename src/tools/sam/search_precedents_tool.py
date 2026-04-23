"""SAM DynamicTool wrapper for the search_precedents tool.

Exposes the search_precedents function as a SAM-compatible tool that
can be registered with the Solace Agent Mesh orchestrator.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Key under which per-session precedent source metadata is stashed on the
# SAM tool_context.state. The orchestrator lifts this into
# CaseState.precedent_source_metadata after the agent turn completes.
PRECEDENT_META_STATE_KEY = "precedent_source_metadata"


def _merge_precedent_meta(
    existing: dict[str, Any] | None,
    new: dict[str, Any],
) -> dict[str, Any]:
    """Worst-of merge across multiple search_precedents calls in one session.

    - First call wins for the initial snapshot.
    - Any subsequent call with source_failed=True escalates the merged
      record to source_failed=True and adopts that call's pair_status.
    - Any other fields flow through from the existing record untouched.
    """
    if existing is None:
        return dict(new)
    if new.get("source_failed"):
        existing["source_failed"] = True
        existing["pair_status"] = new.get("pair_status", existing.get("pair_status"))
    return existing


# Parameter schema describing the search_precedents tool interface.
# Uses plain dicts mirroring google.genai.types.Schema structure so
# this module works without a hard dependency on the ADK package.
SEARCH_PRECEDENTS_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "query": {
            "type": "STRING",
            "description": "Targeted query for legal concepts or statutory provisions",
        },
        "domain": {
            "type": "STRING",
            "description": "Legal domain: 'small_claims' | 'traffic'",
        },
        "vector_store_id": {
            "type": "STRING",
            "description": "Domain vector store ID for the PAIR circuit-breaker fallback (injected by runner)",
        },
        "max_results": {
            "type": "INTEGER",
            "description": "Maximum number of precedents to return",
        },
    },
    "required": ["query", "domain"],
}


class SearchPrecedentsTool:
    """SAM-compatible tool that delegates to search_precedents.

    Implements the DynamicTool protocol expected by solace-agent-mesh:
    - tool_name / tool_description properties
    - parameters_schema property
    - async init / cleanup lifecycle hooks
    - _run_async_impl for execution
    """

    @property
    def tool_name(self) -> str:
        return "search_precedents"

    @property
    def tool_description(self) -> str:
        return "Query the PAIR Search API for binding higher court case law matching fact patterns."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        """Return the parameter schema for tool registration.

        Returns a dict representation compatible with google.genai.types.Schema.
        When the ADK is available, callers can convert this to a Schema object.
        """
        return SEARCH_PRECEDENTS_SCHEMA

    async def init(self, component: Any = None, tool_config: Any = None) -> None:
        """Initialize the tool. No-op: Redis is created per-call."""
        logger.debug("SearchPrecedentsTool initialized")

    async def cleanup(self, component: Any = None, tool_config: Any = None) -> None:
        """Clean up the tool. No-op: no persistent connections to release."""
        logger.debug("SearchPrecedentsTool cleaned up")

    async def _run_async_impl(
        self,
        *,
        args: dict[str, Any],
        tool_context: Any = None,
    ) -> Any:
        """Execute the search_precedents tool with the given arguments.

        Args-first / state-fallback for vector_store_id (D11):
        1. Use vector_store_id from args if provided (in-process path).
        2. Fall back to tool_context.state["domain_vector_store_id"] (mesh path).
        3. If absent, proceed without it (PAIR is PAIR-primary; fallback uses allow_global_fallback).

        Source metadata is stashed on tool_context.state under PRECEDENT_META_STATE_KEY
        so the orchestrator can lift it into CaseState.precedent_source_metadata.
        """
        from src.tools.search_precedents import search_precedents_with_meta

        # Args-first / state-fallback
        if "vector_store_id" not in args or not args.get("vector_store_id"):
            state = getattr(tool_context, "state", None)
            if isinstance(state, dict):
                vsid = state.get("domain_vector_store_id")
                if vsid:
                    args = {**args, "vector_store_id": vsid}

        search_result = await search_precedents_with_meta(**args)

        state = getattr(tool_context, "state", None)
        if isinstance(state, dict):
            state[PRECEDENT_META_STATE_KEY] = _merge_precedent_meta(
                state.get(PRECEDENT_META_STATE_KEY),
                search_result.metadata,
            )

        return search_result.precedents
