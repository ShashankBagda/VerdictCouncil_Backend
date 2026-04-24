"""Terminal node — emits pipeline-level halt SSE event.

Reached when any node sets state["halt"]. Ported from
mesh_runner._emit_terminal (640-666).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.api.schemas.pipeline_events import PipelineProgressEvent
from src.pipeline.graph.prompts import AGENT_ORDER
from src.pipeline.graph.state import GraphState
from src.services.pipeline_events import publish_progress


async def terminal(state: GraphState) -> dict[str, Any]:
    """Emit the run-level terminal SSE event and return unmodified state."""
    case = state["case"]
    halt = state.get("halt") or {}
    reason = halt.get("reason", "unknown")
    stopped_at = halt.get("stopped_at", "unknown")
    error = halt.get("error")

    event = PipelineProgressEvent(
        case_id=case.case_id,  # type: ignore[arg-type]
        agent="pipeline",
        phase="terminal",
        step=None,
        total=len(AGENT_ORDER),
        ts=datetime.now(UTC),
        error=error,
        detail={"reason": reason, "stopped_at": stopped_at},
    )
    await publish_progress(event)
    return {}
