"""Bridge `graph.astream(stream_mode="custom")` → existing Redis publishers.

Phase nodes (Sprint 1 1.A1.4) emit lifecycle events via
`langgraph.config.get_stream_writer()(...)`. The adapter drains the
custom stream and re-publishes each event to Redis using the same
`publish_progress` / `publish_agent_event` channels SSE consumers
already subscribe to.

Two invariants codex P1-4 + the source-driven audit (U-4) flagged:

1. Cancellation propagates -- `asyncio.CancelledError` must surface so
   the FastAPI handler can shut down promptly. We don't catch it.
2. Graph exceptions propagate -- a runtime error inside the graph must
   not be silently flattened to "stream ended". We don't catch them.

Unrecognized chunks (anything without a `kind` we know about) are
dropped silently so framework-internal items LangGraph may yield don't
crash the bridge.
"""

from __future__ import annotations

import logging
from typing import Any

from src.api.schemas.pipeline_events import PipelineProgressEvent
from src.services.pipeline_events import publish_agent_event, publish_progress

logger = logging.getLogger(__name__)


def _coerce_progress(chunk: Any) -> PipelineProgressEvent | None:
    """Normalize a stream chunk into a `PipelineProgressEvent` if possible."""
    if isinstance(chunk, PipelineProgressEvent):
        return chunk
    if isinstance(chunk, dict) and chunk.get("kind") == "progress":
        try:
            return PipelineProgressEvent.model_validate(chunk)
        except Exception:
            logger.exception("failed to coerce progress chunk: %r", chunk)
    return None


async def _dispatch(case_id: str, chunk: Any) -> None:
    """Send one stream chunk to the appropriate Redis publisher."""
    progress = _coerce_progress(chunk)
    if progress is not None:
        await publish_progress(progress)
        return

    if isinstance(chunk, dict) and chunk.get("kind") in {"agent", "narration"}:
        await publish_agent_event(case_id, chunk)
        return

    # Unrecognized — drop silently. LangGraph framework chatter shouldn't
    # break the bridge.
    return


async def stream_to_sse(
    *,
    graph: Any,
    initial_state: Any,
    config: dict[str, Any],
    case_id: str,
) -> None:
    """Drain `graph.astream(stream_mode="custom")` and publish to Redis.

    Args:
        graph: Compiled LangGraph (must support `astream(stream_mode=...)`).
        initial_state: First-turn graph input.
        config: LangGraph config; should carry `configurable.thread_id` so the
            checkpointer persists per-case state.
        case_id: Used to address the Redis pub/sub channel for free-form
            agent / narration events.

    Returns:
        None. Cancellation and graph exceptions propagate to the caller.
    """
    async for chunk in graph.astream(initial_state, config=config, stream_mode="custom"):
        await _dispatch(case_id, chunk)
