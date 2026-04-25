"""Build and compile the VerdictCouncil LangGraph StateGraph (1.A1.7 topology).

The 6-phase topology with 4 HITL gates:

    START → intake → gate1{pause,apply}
        gate1.advance → research_dispatch
        gate1.rerun   → intake
        gate1.halt    → terminal

    research_dispatch ─(Send fan-out)→ research_{evidence,facts,witnesses,law}
        → research_join → gate2{pause,apply}
        gate2.advance → synthesis
        gate2.rerun   → research_dispatch
        gate2.halt    → terminal

    synthesis → gate3{pause,apply}
        gate3.advance → auditor
        gate3.rerun   → synthesis
        gate3.halt    → terminal

    auditor → gate4{pause,apply}
        gate4.advance → END
        gate4.rerun   → auditor
        gate4.halt    → terminal

    terminal → END

The research fan-out follows the V-4 contract: `add_conditional_edges`
from `research_dispatch` via `route_to_research_subagents` (which returns
`list[Send]`). Reducer-backed `research_parts` accumulates the four
parallel branches; `research_join` reads the dict-keyed accumulator and
writes a merged `ResearchOutput` (1.A1.5).

Gate pause nodes call `interrupt(...)`; gate apply nodes return
`Command(goto=...)`. Sprint 1 covers the contract (advance / rerun /
halt). Full review-surface payloads, idempotent status upserts, and the
frontend wiring are 4.A3 (Sprint 4) work.

Sprint 1 phase-output → CaseState integration is deliberately out of
scope here. `make_phase_node(phase)` writes `{phase}_output` to its own
GraphState slot; consumption into `case` is Sprint 2.
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from src.pipeline.graph.agents.factory import make_phase_node
from src.pipeline.graph.nodes.gates import make_gate_apply, make_gate_pause
from src.pipeline.graph.nodes.terminal import terminal
from src.pipeline.graph.research import (
    RESEARCH_SCOPES,
    RESEARCH_SUBAGENT_NODES,
    make_research_node,
    research_dispatch_node,
    research_join_node,
    route_to_research_subagents,
)
from src.pipeline.graph.state import GraphState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retry policy — applied to every LLM-calling node so transient OpenAI /
# network errors auto-recover without leaking up to the runner. Preserved
# from the legacy builder (:148) as required by 1.A1.7 acceptance.
# ---------------------------------------------------------------------------

_FRONTIER_RETRY = RetryPolicy(max_attempts=2, initial_interval=1.0)


def build_graph(config: dict | None = None, *, checkpointer=None):
    """Build and compile the VerdictCouncil StateGraph.

    Three call patterns:

    - **Production / runner**: `build_graph(checkpointer=<saver>)` — the
      `GraphPipelineRunner` passes a saver directly; `config` is unused.
    - **Production / lifespan singleton**: `build_graph()` — both args
      omitted; falls back to `checkpointer.get_checkpointer()` (the
      FastAPI lifespan / arq startup hook installs the AsyncPostgresSaver).
    - **LangGraph CLI (`langgraph dev`)**: `build_graph(config_dict)` —
      the CLI invokes factories detected as accepting a `config: RunnableConfig`
      positionally. We accept the dict, ignore its contents, and compile
      without a checkpointer. The CLI injects its own InMemorySaver at
      runtime, which is what enables `interrupt()` to work in Studio.

    Args:
        config: LangGraph `RunnableConfig` (CLI path only); ignored.
        checkpointer: Optional `BaseCheckpointSaver`. Keyword-only.
    """
    from src.pipeline.graph.checkpointer import get_checkpointer

    # CLI path: `config` is set, `checkpointer` is None. Compile WITHOUT a
    # checkpointer so the CLI runtime can inject its own. Don't fall back
    # to the module singleton — that would clash with the CLI's saver and
    # was the cause of the 1.DEP1.2 TypeError ("dict is not a saver").
    if checkpointer is None and config is None:
        checkpointer = get_checkpointer()

    graph = StateGraph(GraphState)

    # ------------------------------------------------------------------
    # Phase nodes (LLM-calling) + research fan-out scaffolding
    # ------------------------------------------------------------------
    graph.add_node("intake", make_phase_node("intake"), retry_policy=_FRONTIER_RETRY)
    graph.add_node("research_dispatch", research_dispatch_node)
    for scope in RESEARCH_SCOPES:
        graph.add_node(
            RESEARCH_SUBAGENT_NODES[scope],
            make_research_node(scope),
            retry_policy=_FRONTIER_RETRY,
        )
    graph.add_node("research_join", research_join_node)
    graph.add_node("synthesis", make_phase_node("synthesis"), retry_policy=_FRONTIER_RETRY)
    graph.add_node("auditor", make_phase_node("audit"), retry_policy=_FRONTIER_RETRY)

    # ------------------------------------------------------------------
    # Gate pause + apply pairs (HITL)
    # ------------------------------------------------------------------
    graph.add_node("gate1_pause", make_gate_pause("gate1"))
    graph.add_node(
        "gate1_apply",
        make_gate_apply("gate1", advance_target="research_dispatch", rerun_target="intake"),
    )
    graph.add_node("gate2_pause", make_gate_pause("gate2"))
    graph.add_node(
        "gate2_apply",
        make_gate_apply("gate2", advance_target="synthesis", rerun_target="research_dispatch"),
    )
    graph.add_node("gate3_pause", make_gate_pause("gate3"))
    graph.add_node(
        "gate3_apply",
        make_gate_apply("gate3", advance_target="auditor", rerun_target="synthesis"),
    )
    graph.add_node("gate4_pause", make_gate_pause("gate4"))
    graph.add_node(
        "gate4_apply",
        make_gate_apply("gate4", advance_target=END, rerun_target="auditor"),
    )

    graph.add_node("terminal", terminal)

    # ------------------------------------------------------------------
    # Edges
    # ------------------------------------------------------------------
    graph.add_edge(START, "intake")

    # Phase 1 (intake) → gate1
    graph.add_edge("intake", "gate1_pause")
    graph.add_edge("gate1_pause", "gate1_apply")
    # gate1_apply is a Command-returning node — its `goto` handles routing.

    # Phase 2 (research) — Send fan-out via conditional edges (V-4)
    graph.add_conditional_edges(
        "research_dispatch",
        route_to_research_subagents,
        list(RESEARCH_SUBAGENT_NODES.values()),
    )
    for scope in RESEARCH_SCOPES:
        graph.add_edge(RESEARCH_SUBAGENT_NODES[scope], "research_join")
    graph.add_edge("research_join", "gate2_pause")
    graph.add_edge("gate2_pause", "gate2_apply")

    # Phase 3 (synthesis) → gate3
    graph.add_edge("synthesis", "gate3_pause")
    graph.add_edge("gate3_pause", "gate3_apply")

    # Phase 4 (auditor) → gate4
    graph.add_edge("auditor", "gate4_pause")
    graph.add_edge("gate4_pause", "gate4_apply")

    # Terminal sink
    graph.add_edge("terminal", END)

    return graph.compile(checkpointer=checkpointer)
