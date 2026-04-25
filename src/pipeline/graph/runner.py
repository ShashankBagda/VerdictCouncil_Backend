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
from src.pipeline.graph.runner_stream_adapter import stream_to_sse
from src.pipeline.graph.state import GraphState
from src.pipeline.observability import pipeline_run
from src.shared.case_state import CaseState, CaseStatusEnum
from src.shared.config import settings

logger = logging.getLogger(__name__)

# Gate name → the node name that begins execution for that gate (1.A1.7
# topology). `run_gate` / `run_what_if` use this when the caller does not
# specify `start_agent`. NOTE: jump-to-start_agent routing is currently
# unwired — the new builder always begins from `intake`. Reactivating
# start-from-node semantics is a follow-up (likely Sprint 4 / 4.A3 with
# the gate UX work).
_GATE_ENTRY_NODE: dict[str, str] = {
    "gate1": "intake",
    "gate2": "research_dispatch",
    "gate3": "synthesis",
    "gate4": "auditor",
}


_VALID_RUNTIMES = ("in_process", "cloud")


class GraphPipelineRunner:
    """LangGraph-backed pipeline runner with PipelineRunner-compatible surface."""

    def __init__(self, checkpointer=None, *, mode: str | None = None) -> None:
        """Build the compiled graph (in-process) or prepare cloud routing.

        Args:
            checkpointer: Optional `BaseCheckpointSaver`. None defers to the
                process-wide singleton set by the FastAPI lifespan / arq
                startup hooks. Tests pass an `InMemorySaver` here.
            mode: Override the runtime mode (`"in_process"` | `"cloud"`).
                Defaults to `settings.graph_runtime`. Sprint 1 1.DEP1.3:
                in-process is fully wired; cloud raises NotImplementedError
                until Sprint 5 5.DEP.6 fills in the HTTP-API client.
        """
        resolved = mode or settings.graph_runtime
        if resolved not in _VALID_RUNTIMES:
            raise ValueError(
                f"Unknown graph_runtime: {resolved!r}; expected one of {_VALID_RUNTIMES}"
            )
        self._mode: str = resolved
        if resolved == "in_process":
            self._graph = build_graph(checkpointer=checkpointer)
        else:
            # Cloud branch — wired in Sprint 5 5.DEP.6 to call the LangGraph
            # Cloud HTTP API. Sprint 1 just declares the seam and fails loud
            # if anyone flips the env var early.
            self._graph = None

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
        """Drive the compiled graph via streaming and return the final CaseState.

        Threads `thread_id = case_id` into the LangGraph config so the
        compile-time checkpointer can persist per-case state across gates
        and reruns. Without this, `interrupt()` / `Command(resume=...)`
        cannot work.

        Sprint 1 1.A1.8: the runner uses `stream_to_sse(...)` so SSE
        side-effects flow through the streaming adapter, then the terminal
        state is read back from the checkpointer via `aget_state(config)`.
        Replaces the legacy `graph.ainvoke(...)` call. SSE emission today
        happens via direct `publish_*` calls inside the agent middleware;
        the stream-writer-based pattern (V-8) becomes load-bearing once
        nodes start writing through `get_stream_writer()` in a later
        sprint, but the runner's invocation surface is already aligned.
        """
        if self._mode == "cloud":
            # TODO(5.DEP.6): call the LangGraph Cloud HTTP API instead of
            # the in-process graph. Pass the same metadata + thread_id so
            # the cloud trace surface is consistent with local runs.
            raise NotImplementedError(
                "graph_runtime='cloud' is reserved for Sprint 5 5.DEP.6; "
                "set settings.graph_runtime='in_process' (the default) for now."
            )

        case = initial_state["case"]
        case_id = str(case.case_id)
        run_id = initial_state.get("run_id") or ""
        # `metadata` (1.C3a.1) tags every LangSmith trace with env / case_id
        # / run_id so a single `verdictcouncil` project can be filtered per
        # environment without splitting the project namespace.
        config = {
            "configurable": {"thread_id": case_id},
            "metadata": {
                "env": settings.app_env,
                "case_id": case_id,
                "run_id": run_id,
            },
        }

        await stream_to_sse(
            graph=self._graph,
            initial_state=initial_state,
            config=config,
            case_id=case_id,
        )

        snapshot = await self._graph.aget_state(config)
        return snapshot.values["case"]

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
