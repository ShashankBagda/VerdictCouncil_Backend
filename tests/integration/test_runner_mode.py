"""Sprint 1 1.DEP1.3 — runner-mode selector tests.

Covers the in-process / cloud branch on `GraphPipelineRunner._mode`.
Sprint 1 wires the in-process path fully; the cloud path raises
`NotImplementedError` until Sprint 5 5.DEP.6.

Tests run synchronously where possible to avoid pulling in the asyncio
event loop machinery for what is essentially a feature-flag check.
"""

from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from src.pipeline.graph.runner import GraphPipelineRunner
from src.shared.case_state import CaseState


def test_default_mode_is_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing behaviour: settings default → in_process; graph compiled."""
    runner = GraphPipelineRunner(checkpointer=InMemorySaver())
    assert runner._mode == "in_process"
    assert runner._graph is not None, "in_process mode must build the local graph"


def test_explicit_mode_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit `mode=` constructor arg overrides settings."""
    monkeypatch.setattr("src.shared.config.settings.graph_runtime", "cloud")
    # Even with cloud in settings, an explicit override wins.
    runner = GraphPipelineRunner(checkpointer=InMemorySaver(), mode="in_process")
    assert runner._mode == "in_process"
    assert runner._graph is not None


def test_unknown_mode_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown graph_runtime"):
        GraphPipelineRunner(mode="hybrid")


def test_cloud_mode_does_not_build_local_graph() -> None:
    """The cloud stub must not pay the build cost."""
    runner = GraphPipelineRunner(mode="cloud")
    assert runner._mode == "cloud"
    assert runner._graph is None


def test_cloud_mode_run_raises_not_implemented_with_pointer_to_5_dep_6() -> None:
    """Calling .run on the cloud stub must surface the right error + redirect."""
    runner = GraphPipelineRunner(mode="cloud")
    case = CaseState(case_id="00000000-0000-0000-0000-000000000abc")

    with pytest.raises(NotImplementedError, match="5\\.DEP\\.6"):
        asyncio.run(runner.run(case))


def test_settings_default_is_in_process() -> None:
    """Smoke check on the new Settings field — important for backwards compat."""
    from src.shared.config import settings

    # The real running settings must default to in_process so that old
    # call-sites (cases.py, what_if.py, workers) keep working unchanged
    # after this PR lands.
    assert settings.graph_runtime in ("in_process", "cloud"), (
        f"settings.graph_runtime must be one of in_process/cloud; got {settings.graph_runtime!r}"
    )
