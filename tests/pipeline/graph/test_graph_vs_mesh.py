"""Parity tests: LangGraph output vs mesh baseline.

Covers:
  - _strip_volatile: volatile key removal before diffing
  - _compute_match_ratio: ratio computation from DeepDiff
  - ShadowRunner._log_diff: MLflow artifact logging (mocked)
  - ShadowRunner.run / run_gate: returns mesh result, handles runner failures

Integration parity test (graph vs mesh on a gold-set fixture) is marked
@pytest.mark.integration and skipped in CI — it requires live LLM + broker.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.pipeline.graph.shadow import (
    ShadowRunner,
    _compute_match_ratio,
    _strip_volatile,
)
from src.shared.case_state import CaseState

# ---------------------------------------------------------------------------
# _strip_volatile
# ---------------------------------------------------------------------------


class TestStripVolatile:
    def test_removes_run_id(self):
        d = {"run_id": "abc", "case_id": "x", "status": "processing"}
        cleaned = _strip_volatile(d)
        assert "run_id" not in cleaned
        assert "case_id" in cleaned

    def test_removes_parent_run_id(self):
        d = {"parent_run_id": "def", "status": "processing"}
        cleaned = _strip_volatile(d)
        assert "parent_run_id" not in cleaned

    def test_audit_log_timestamps_stripped(self):
        d = {
            "audit_log": [
                {"agent": "case-processing", "timestamp": "2025-01-01T00:00:00Z", "action": "x"},
                {"agent": "evidence-analysis", "timestamp": "2025-01-01T00:01:00Z", "tool_calls": []},
            ]
        }
        cleaned = _strip_volatile(d)
        for entry in cleaned["audit_log"]:
            assert "timestamp" not in entry
            assert "tool_calls" not in entry
            assert "agent" in entry

    def test_audit_log_none_is_unchanged(self):
        d = {"audit_log": None}
        cleaned = _strip_volatile(d)
        assert cleaned["audit_log"] is None

    def test_audit_log_empty_list_is_unchanged(self):
        d = {"audit_log": []}
        cleaned = _strip_volatile(d)
        assert cleaned["audit_log"] == []

    def test_orchestration_timing_keys_stripped(self):
        d = {
            "case_metadata": {
                "some_key": "value",
                "orchestration": {
                    "pipeline_start_time": "t1",
                    "pipeline_end_time": "t2",
                    "total_duration_seconds": 42.0,
                    "other": "kept",
                },
            }
        }
        cleaned = _strip_volatile(d)
        orch = cleaned["case_metadata"]["orchestration"]
        assert "pipeline_start_time" not in orch
        assert "pipeline_end_time" not in orch
        assert "total_duration_seconds" not in orch
        assert orch["other"] == "kept"

    def test_non_volatile_fields_preserved(self):
        d = {
            "case_id": "case-123",
            "status": "processing",
            "arguments": [{"summary": "arg1"}],
        }
        cleaned = _strip_volatile(d)
        assert cleaned == d


# ---------------------------------------------------------------------------
# _compute_match_ratio
# ---------------------------------------------------------------------------


class TestComputeMatchRatio:
    def test_empty_diff_returns_1(self):
        from deepdiff import DeepDiff

        diff = DeepDiff({}, {})
        assert _compute_match_ratio(diff) == 1.0

    def test_identical_dicts_return_1(self):
        from deepdiff import DeepDiff

        a = {"k": "v", "n": 1}
        diff = DeepDiff(a, a, ignore_order=True)
        assert _compute_match_ratio(diff) == 1.0

    def test_ratio_below_1_for_diffs(self):
        from deepdiff import DeepDiff

        a = {"field": "original"}
        b = {"field": "changed"}
        diff = DeepDiff(a, b, ignore_order=True)
        ratio = _compute_match_ratio(diff)
        assert 0.0 <= ratio < 1.0

    def test_ratio_is_float_in_range(self):
        from deepdiff import DeepDiff

        a = {f"k{i}": f"v{i}" for i in range(20)}
        b = {f"k{i}": f"x{i}" for i in range(20)}
        diff = DeepDiff(a, b, ignore_order=True)
        ratio = _compute_match_ratio(diff)
        assert 0.0 <= ratio <= 1.0


# ---------------------------------------------------------------------------
# ShadowRunner helpers (mocked runners)
# ---------------------------------------------------------------------------


def _make_case(**kwargs: Any) -> CaseState:
    return CaseState(case_id="case-shadow-test", **kwargs)  # type: ignore[arg-type]


class TestShadowRunnerRun:
    @pytest.mark.asyncio
    async def test_returns_mesh_result_when_both_succeed(self):
        mesh_state = _make_case(status="processing")
        graph_state = _make_case(status="processing")

        runner = ShadowRunner.__new__(ShadowRunner)
        runner._mesh_runner = MagicMock()
        runner._graph_runner = MagicMock()
        runner._mesh_runner.run = AsyncMock(return_value=mesh_state)
        runner._graph_runner.run = AsyncMock(return_value=graph_state)
        runner._log_diff = AsyncMock()

        result = await runner.run(_make_case())
        assert result is mesh_state

    @pytest.mark.asyncio
    async def test_falls_back_to_graph_when_mesh_fails(self):
        graph_state = _make_case(status="processing")

        runner = ShadowRunner.__new__(ShadowRunner)
        runner._mesh_runner = MagicMock()
        runner._graph_runner = MagicMock()
        runner._mesh_runner.run = AsyncMock(side_effect=RuntimeError("broker down"))
        runner._graph_runner.run = AsyncMock(return_value=graph_state)
        runner._log_diff = AsyncMock()

        result = await runner.run(_make_case())
        assert result is graph_state

    @pytest.mark.asyncio
    async def test_returns_mesh_result_when_graph_fails(self):
        mesh_state = _make_case(status="processing")

        runner = ShadowRunner.__new__(ShadowRunner)
        runner._mesh_runner = MagicMock()
        runner._graph_runner = MagicMock()
        runner._mesh_runner.run = AsyncMock(return_value=mesh_state)
        runner._graph_runner.run = AsyncMock(side_effect=RuntimeError("graph error"))
        runner._log_diff = AsyncMock()

        result = await runner.run(_make_case())
        assert result is mesh_state

    @pytest.mark.asyncio
    async def test_reraises_when_both_fail(self):
        runner = ShadowRunner.__new__(ShadowRunner)
        runner._mesh_runner = MagicMock()
        runner._graph_runner = MagicMock()
        runner._mesh_runner.run = AsyncMock(side_effect=RuntimeError("mesh dead"))
        runner._graph_runner.run = AsyncMock(side_effect=RuntimeError("graph dead"))
        runner._log_diff = AsyncMock()

        with pytest.raises(RuntimeError, match="mesh dead"):
            await runner.run(_make_case())

    @pytest.mark.asyncio
    async def test_log_diff_called_when_both_succeed(self):
        mesh_state = _make_case()
        graph_state = _make_case()

        runner = ShadowRunner.__new__(ShadowRunner)
        runner._mesh_runner = MagicMock()
        runner._graph_runner = MagicMock()
        runner._mesh_runner.run = AsyncMock(return_value=mesh_state)
        runner._graph_runner.run = AsyncMock(return_value=graph_state)
        runner._log_diff = AsyncMock()

        await runner.run(_make_case())
        runner._log_diff.assert_awaited_once()


class TestShadowRunnerRunGate:
    @pytest.mark.asyncio
    async def test_returns_mesh_result_for_gate(self):
        mesh_state = _make_case()
        graph_state = _make_case()

        runner = ShadowRunner.__new__(ShadowRunner)
        runner._mesh_runner = MagicMock()
        runner._graph_runner = MagicMock()
        runner._mesh_runner.run_gate = AsyncMock(return_value=mesh_state)
        runner._graph_runner.run_gate = AsyncMock(return_value=graph_state)
        runner._log_diff = AsyncMock()

        result = await runner.run_gate(_make_case(), "gate1")
        assert result is mesh_state

    @pytest.mark.asyncio
    async def test_passes_gate_name_and_kwargs_to_both(self):
        mesh_state = _make_case()
        graph_state = _make_case()

        runner = ShadowRunner.__new__(ShadowRunner)
        runner._mesh_runner = MagicMock()
        runner._graph_runner = MagicMock()
        runner._mesh_runner.run_gate = AsyncMock(return_value=mesh_state)
        runner._graph_runner.run_gate = AsyncMock(return_value=graph_state)
        runner._log_diff = AsyncMock()

        await runner.run_gate(_make_case(), "gate2", start_agent="evidence_analysis", extra_instructions="be thorough")

        # Mesh runner receives positional args (case_state, gate_name, start_agent, extra_instructions)
        _, mesh_args, _ = runner._mesh_runner.run_gate.mock_calls[0]
        assert mesh_args[1] == "gate2"
        assert mesh_args[2] == "evidence_analysis"

        # Graph runner receives kwargs
        _, graph_args, graph_kwargs = runner._graph_runner.run_gate.mock_calls[0]
        assert graph_args[1] == "gate2"
        assert graph_kwargs.get("start_agent") == "evidence_analysis"


# ---------------------------------------------------------------------------
# ShadowRunner._log_diff (MLflow logging)
# ---------------------------------------------------------------------------


class TestLogDiff:
    @pytest.mark.asyncio
    async def test_log_diff_skipped_when_mlflow_disabled(self):
        """When mlflow_enabled=False, _log_diff returns without MLflow calls."""
        runner = ShadowRunner.__new__(ShadowRunner)

        with patch("src.shared.config.settings") as mock_settings:
            mock_settings.mlflow_enabled = False
            await runner._log_diff(
                case_id="c1",
                run_id="r1",
                mesh=_make_case(),
                graph=_make_case(),
            )
        # No MLflow import or call — nothing to assert other than no exception

    @pytest.mark.asyncio
    async def test_log_diff_does_not_raise_on_mlflow_error(self):
        """MLflow failures in _log_diff must not propagate."""
        import sys

        runner = ShadowRunner.__new__(ShadowRunner)

        mock_mlflow = MagicMock()
        mock_mlflow.start_run.side_effect = RuntimeError("mlflow down")

        with (
            patch("src.shared.config.settings") as mock_settings,
            patch.dict(sys.modules, {"mlflow": mock_mlflow}),
        ):
            mock_settings.mlflow_enabled = True
            # Must not raise
            await runner._log_diff(
                case_id="c2",
                run_id="r2",
                mesh=_make_case(),
                graph=_make_case(),
            )


# ---------------------------------------------------------------------------
# Integration parity (skipped in CI — requires live LLM + broker)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skip(reason="Requires live LLM, Redis, and broker (run manually in staging)")
class TestGraphVsMeshParity:
    """Full integration: run both runners on a gold-set fixture, assert ≥95% match."""

    @pytest.mark.asyncio
    async def test_gate1_parity_on_gold_fixture(self):
        from deepdiff import DeepDiff

        from src.pipeline.graph.runner import GraphPipelineRunner
        from src.pipeline.runner import PipelineRunner
        from src.shared.case_state import CaseState

        # Load a gold-set fixture (must be a realistic CaseState with domain set)
        gold_case = CaseState(
            case_id="gold-parity-test",
            domain="traffic_violation",
            domain_vector_store_id="vs_placeholder",
            status="processing",
        )

        mesh_result = await PipelineRunner().run(gold_case)
        graph_result = await GraphPipelineRunner().run(gold_case)

        mesh_clean = _strip_volatile(mesh_result.model_dump())
        graph_clean = _strip_volatile(graph_result.model_dump())

        diff = DeepDiff(mesh_clean, graph_clean, ignore_order=True, significant_digits=3)
        ratio = _compute_match_ratio(diff)

        assert ratio >= 0.95, f"Graph/mesh field-match ratio {ratio:.3f} < 0.95. Diff: {diff.to_dict()}"
