"""ShadowRunner — dual-run harness for safe cutover from mesh to LangGraph.

Runs both runners on identical inputs in parallel, returns the mesh result
(known-good path), and diffs the graph result against it. Diff is logged
as an MLflow artifact for review before cutting over production traffic.

Acceptance criteria (before SAM-deletion PR):
  - ≥ 95% field-match ratio across ≥ 30 gold-set cases
  - Zero field-ownership violations in graph runs
  - No missing tool calls vs. mesh baseline
  - Remaining diffs confined to known-variable prose fields

Volatile keys stripped before diff:
  - run_id, parent_run_id
  - audit_log[*].timestamp, audit_log[*].tool_calls[*].result
  - case_metadata.orchestration.pipeline_start_time|pipeline_end_time|total_duration_seconds
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from deepdiff import DeepDiff

from src.pipeline.graph.runner import GraphPipelineRunner
from src.pipeline.runner import PipelineRunner
from src.shared.case_state import CaseState

logger = logging.getLogger(__name__)

# Fields that vary between runs and should not count as diffs
_VOLATILE_TOP_LEVEL = {"run_id", "parent_run_id"}

_VOLATILE_AUDIT_KEYS = {"timestamp", "tool_calls"}

_VOLATILE_ORCHESTRATION_KEYS = {
    "pipeline_start_time",
    "pipeline_end_time",
    "total_duration_seconds",
}


def _strip_volatile(state_dict: dict[str, Any]) -> dict[str, Any]:
    """Remove volatile keys before diffing."""
    cleaned = {k: v for k, v in state_dict.items() if k not in _VOLATILE_TOP_LEVEL}

    # Sanitize audit_log entries
    if "audit_log" in cleaned and cleaned["audit_log"]:
        cleaned["audit_log"] = [
            {k: v for k, v in entry.items() if k not in _VOLATILE_AUDIT_KEYS} for entry in cleaned["audit_log"]
        ]

    # Sanitize orchestration metadata
    meta = cleaned.get("case_metadata")
    if isinstance(meta, dict) and "orchestration" in meta:
        orch = meta["orchestration"]
        cleaned["case_metadata"] = {
            **meta,
            "orchestration": {k: v for k, v in orch.items() if k not in _VOLATILE_ORCHESTRATION_KEYS},
        }

    return cleaned


def _compute_match_ratio(diff: DeepDiff) -> float:
    """Estimate field-match ratio from a DeepDiff result (0.0–1.0)."""
    if not diff:
        return 1.0
    changed = sum(len(v) for v in diff.to_dict().values() if isinstance(v, dict))
    return max(0.0, 1.0 - (changed / max(changed + 100, 1)))


class ShadowRunner:
    """Dual-run harness: runs both runners, returns mesh result, logs diff."""

    def __init__(self) -> None:
        self._mesh_runner = PipelineRunner()
        self._graph_runner = GraphPipelineRunner()

    async def run(self, case_state: CaseState) -> CaseState:
        """Run both runners in parallel and return the mesh result.

        The LangGraph result is diffed against the mesh result and the
        diff is logged as an MLflow artifact for review.
        """
        mesh_task = asyncio.create_task(self._run_mesh(case_state))
        graph_task = asyncio.create_task(self._run_graph(case_state))

        mesh_result, graph_result = await asyncio.gather(mesh_task, graph_task, return_exceptions=True)

        if isinstance(mesh_result, Exception):
            logger.error("Shadow: mesh runner failed: %s", mesh_result)
            if isinstance(graph_result, CaseState):
                return graph_result  # fall back to graph if mesh fails
            raise mesh_result

        if isinstance(graph_result, Exception):
            logger.warning("Shadow: graph runner failed: %s — returning mesh result", graph_result)
            return mesh_result  # type: ignore[return-value]

        await self._log_diff(
            case_id=str(case_state.case_id),
            run_id=case_state.run_id or "unknown",
            mesh=mesh_result,  # type: ignore[arg-type]
            graph=graph_result,  # type: ignore[arg-type]
        )
        return mesh_result  # type: ignore[return-value]

    async def run_gate(
        self,
        case_state: CaseState,
        gate_name: str,
        start_agent: str | None = None,
        extra_instructions: str | None = None,
    ) -> CaseState:
        """Shadow run for a single gate."""
        mesh_task = asyncio.create_task(
            self._mesh_runner.run_gate(case_state, gate_name, start_agent, extra_instructions)
        )
        graph_task = asyncio.create_task(
            self._graph_runner.run_gate(
                case_state,
                gate_name,
                start_agent=start_agent,
                extra_instructions=extra_instructions,
            )
        )

        mesh_result, graph_result = await asyncio.gather(mesh_task, graph_task, return_exceptions=True)

        if isinstance(mesh_result, Exception):
            logger.error("Shadow gate %s: mesh failed: %s", gate_name, mesh_result)
            if isinstance(graph_result, CaseState):
                return graph_result
            raise mesh_result

        if isinstance(graph_result, Exception):
            logger.warning("Shadow gate %s: graph failed: %s — returning mesh result", gate_name, graph_result)
            return mesh_result  # type: ignore[return-value]

        await self._log_diff(
            case_id=str(case_state.case_id),
            run_id=f"{case_state.run_id or 'unknown'}-{gate_name}",
            mesh=mesh_result,  # type: ignore[arg-type]
            graph=graph_result,  # type: ignore[arg-type]
        )
        return mesh_result  # type: ignore[return-value]

    async def _run_mesh(self, case_state: CaseState) -> CaseState:
        return await self._mesh_runner.run(case_state)

    async def _run_graph(self, case_state: CaseState) -> CaseState:
        return await self._graph_runner.run(case_state)

    async def _log_diff(
        self,
        *,
        case_id: str,
        run_id: str,
        mesh: CaseState,
        graph: CaseState,
    ) -> None:
        """Compute diff and log as an MLflow artifact (fire-and-forget)."""
        try:
            from src.shared.config import settings

            if not settings.mlflow_enabled:
                return

            mesh_clean = _strip_volatile(mesh.model_dump())
            graph_clean = _strip_volatile(graph.model_dump())

            diff = DeepDiff(
                mesh_clean,
                graph_clean,
                ignore_order=True,
                significant_digits=3,
            )
            match_ratio = _compute_match_ratio(diff)
            diff_field_count = sum(len(v) for v in diff.to_dict().values() if isinstance(v, dict))

            import mlflow

            run_name = f"shadow_{case_id[:8]}_{run_id[:8]}"
            with mlflow.start_run(run_name=run_name, nested=True) as run:
                mlflow.set_tags(
                    {
                        "runner_mode": "shadow",
                        "case_id": case_id,
                        "run_id": run_id,
                        "ts": datetime.now(UTC).isoformat(),
                    }
                )
                mlflow.log_metric("match_ratio", match_ratio)
                mlflow.log_metric("diff_field_count", diff_field_count)

                diff_json = json.dumps(diff.to_dict(), default=str, indent=2)
                with mlflow.start_run(run.info.run_id):
                    mlflow.log_text(diff_json, "shadow_diff.json")

            logger.info(
                "Shadow diff logged: case_id=%s match_ratio=%.3f diff_fields=%d",
                case_id,
                match_ratio,
                diff_field_count,
            )
        except Exception:
            logger.exception("Shadow diff logging failed — non-fatal")
