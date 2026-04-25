"""GraphPipelineRunner — public interface matching PipelineRunner's surface.

Wraps the compiled LangGraph StateGraph. Provides the same `run`,
`run_gate`, and `run_what_if` entry-points as the existing PipelineRunner
so API routes and workers can swap runners behind the `settings.runner`
feature flag without changing call sites.
"""

from __future__ import annotations

import logging
import uuid

from src.pipeline.graph.builder import build_graph
from src.pipeline.graph.state import GraphState
from src.pipeline.observability import pipeline_run
from src.shared.case_state import CaseState, CaseStatusEnum

logger = logging.getLogger(__name__)

# Gate name → the node name that begins execution for that gate
_GATE_ENTRY_NODE: dict[str, str] = {
    "gate1": "case_processing",
    "gate2": "gate2_dispatch",
    "gate3": "argument_construction",
    "gate4": "hearing_governance",
}


class GraphPipelineRunner:
    """LangGraph-backed pipeline runner with PipelineRunner-compatible surface."""

    def __init__(self, checkpointer=None) -> None:
        """Build the compiled graph.

        Args:
            checkpointer: Optional `BaseCheckpointSaver`. None defers to the
                process-wide singleton set by the FastAPI lifespan / arq
                startup hooks. Tests pass an `InMemorySaver` here.
        """
        self._graph = build_graph(checkpointer=checkpointer)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_initial_state(
        self,
        case: CaseState,
        run_id: str,
        *,
        start_agent: str | None = None,
        extra_instructions: dict[str, str] | None = None,
        is_resume: bool = False,
    ) -> GraphState:
        return GraphState(
            case=case,
            run_id=run_id,
            extra_instructions=extra_instructions or {},
            retry_counts={},
            halt=None,
            mlflow_run_ids={},
            is_resume=is_resume,
            start_agent=start_agent,
        )

    async def _invoke(self, initial_state: GraphState) -> CaseState:
        """Invoke the compiled graph and return the final CaseState.

        Threads `thread_id = case_id` into the LangGraph config so the
        compile-time checkpointer can persist per-case state across
        gates and reruns. Without this, `interrupt()` / `Command(resume=...)`
        cannot work.
        """
        case = initial_state["case"]
        config = {"configurable": {"thread_id": str(case.case_id)}}
        result = await self._graph.ainvoke(initial_state, config=config)
        return result["case"]

    # ------------------------------------------------------------------
    # Public surface (matches PipelineRunner)
    # ------------------------------------------------------------------

    async def run(self, case_state: CaseState) -> CaseState:
        """Run gate 1 for a new case submission.

        Matches `PipelineRunner.run(case_state)` — only gate 1 executes;
        judge reviews and triggers subsequent gates via `run_gate`.
        """
        run_id = case_state.run_id or str(uuid.uuid4())
        initial_state = self._build_initial_state(case_state, run_id)

        with pipeline_run(
            case_id=str(case_state.case_id or "unknown"),
            run_id=run_id,
            mode="langgraph",
        ):
            result = await self._invoke(initial_state)

        return result

    async def run_gate(
        self,
        case_state: CaseState,
        gate_name: str,
        *,
        start_agent: str | None = None,
        extra_instructions: str | None = None,
    ) -> CaseState:
        """Run one gate's agents then pause for judge review.

        Matches `PipelineRunner.run_gate(case_state, gate_name, start_agent,
        extra_instructions)`. After all gate agents complete, sets
        `case.status = awaiting_review_<gate_name>`.

        Args:
            case_state: Current case state.
            gate_name: "gate1" | "gate2" | "gate3" | "gate4".
            start_agent: Resume from this agent within the gate (for reruns).
            extra_instructions: Corrective instructions for start_agent only.
        """
        run_id = case_state.run_id or str(uuid.uuid4())
        entry_node = start_agent or _GATE_ENTRY_NODE.get(gate_name)

        extra_map: dict[str, str] = {}
        if extra_instructions and start_agent:
            extra_map[start_agent] = extra_instructions

        initial_state = self._build_initial_state(
            case_state,
            run_id,
            start_agent=entry_node,
            extra_instructions=extra_map,
            is_resume=start_agent is not None,
        )

        with pipeline_run(
            case_id=str(case_state.case_id or "unknown"),
            run_id=run_id,
            mode="langgraph",
        ):
            result = await self._invoke(initial_state)

        gate_pause_status = CaseStatusEnum[f"awaiting_review_{gate_name}"]
        result = result.model_copy(update={"status": gate_pause_status})
        logger.info(
            "Gate %s completed for case_id=%s, pausing for judge review",
            gate_name,
            result.case_id,
        )
        return result

    async def run_what_if(
        self,
        case_state: CaseState,
        *,
        start_agent: str,
        run_id: str | None = None,
    ) -> CaseState:
        """Resume from a specific agent for What-If analysis.

        Args:
            case_state: Fully populated CaseState to fork from.
            start_agent: Agent name to start execution from.
            run_id: Optional run ID (creates a new one if omitted).
        """
        run_id = run_id or str(uuid.uuid4())
        initial_state = self._build_initial_state(
            case_state,
            run_id,
            start_agent=start_agent,
            is_resume=True,
        )
        return await self._invoke(initial_state)
