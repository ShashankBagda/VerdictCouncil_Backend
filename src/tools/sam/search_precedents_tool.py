"""SAM DynamicTool wrapper for the search_precedents tool.

Exposes the search_precedents function as a SAM-compatible tool that
can be registered with the Solace Agent Mesh orchestrator.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Key under which precedent source metadata is written into the SAM
# ``tool_context.state`` dict. The orchestrator/gateway is expected to
# read this value after each tool call and merge it into the canonical
# ``CaseState.precedent_source_metadata`` field before the next agent
# fires, mirroring how :class:`PipelineRunner` injects metadata after
# the legal-knowledge agent step.
PRECEDENT_META_STATE_KEY = "precedent_source_metadata"


def _merge_precedent_meta(
    existing: dict[str, Any] | None, incoming: dict[str, Any]
) -> dict[str, Any]:
    """Merge precedent-source metadata across multiple search calls.

    Mirrors the worst-of merge in :class:`PipelineRunner`: the first
    call's metadata is stored verbatim, and subsequent calls only
    escalate ``source_failed`` (and the matching ``pair_status``) when
    a later call also failed. This guarantees that any single source
    failure within a case sticks across the remaining tool calls.
    """
    if existing is None:
        return dict(incoming)
    if incoming.get("source_failed"):
        existing["source_failed"] = True
        existing["pair_status"] = incoming.get(
            "pair_status", existing.get("pair_status")
        )
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

        Calls :func:`search_precedents_with_meta` so the precedent
        source metadata can be propagated to the SAM session state via
        ``tool_context.state[PRECEDENT_META_STATE_KEY]``. The
        orchestrator is expected to copy that value into the canonical
        ``CaseState.precedent_source_metadata`` before the next agent
        runs, so the governance prompt's
        ``precedent_source_metadata.source_failed`` check works on the
        SAM mesh path the same way it does in the in-process pipeline.

        Args:
            args: Dictionary of keyword arguments matching the parameters_schema.
            tool_context: Optional SAM tool context. When provided and
                exposing a ``state`` mapping, precedent source metadata
                is merged into it under :data:`PRECEDENT_META_STATE_KEY`.

        Returns:
            List of precedent dicts (the agent-visible return shape is
            unchanged from the previous implementation).
        """
        from src.tools.search_precedents import search_precedents_with_meta

        result = await search_precedents_with_meta(**args)

        state = getattr(tool_context, "state", None)
        if state is not None:
            try:
                existing = state.get(PRECEDENT_META_STATE_KEY)
                state[PRECEDENT_META_STATE_KEY] = _merge_precedent_meta(
                    existing, result.metadata
                )
            except Exception:  # pragma: no cover - defensive: never fail a tool call
                logger.exception(
                    "Failed to write precedent_source_metadata into tool_context.state"
                )

        return result.precedents
