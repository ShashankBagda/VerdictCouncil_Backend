"""P0.2 acceptance tests — pipeline failure emits phase=failed SSE frame.

Tests _run_case_pipeline directly: patches the GraphPipelineRunner to raise,
captures publish_progress calls, and asserts a phase=failed event is published
before the DB status flip.

Patch targets use source-module paths because async_session, publish_progress,
and GraphPipelineRunner are all lazy-imported inside the function body.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.schemas.pipeline_events import PipelineProgressEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db_case(case_id: uuid.UUID) -> MagicMock:
    db_case = MagicMock()
    db_case.id = case_id
    db_case.status = MagicMock()
    db_case.gate_state = None
    db_case.title = "Test Case"
    db_case.description = "A test description"
    db_case.filed_date = None
    db_case.claim_amount = None
    db_case.consent_to_higher_claim_limit = False
    db_case.offence_code = "RTA-S64"
    db_case.domain = MagicMock()
    db_case.domain.value = "traffic_violation"
    db_case.domain_ref = None
    db_case.documents = []
    db_case.parties = []
    return db_case


def _make_mock_session(db_case: MagicMock) -> AsyncMock:
    """Build a context manager returning a mock DB session."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = db_case
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(return_value=db_case)
    session.commit = AsyncMock()

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# P0.2: phase=failed frame published before DB status flip
# ---------------------------------------------------------------------------


class TestPipelineFailureEmitsFailedFrame:
    async def test_failed_frame_published_on_runner_exception(self):
        """When GraphPipelineRunner.run raises, publish_progress must be called
        with phase='failed' before the DB status is flipped to 'failed'.
        """
        from src.api.routes.cases import _run_case_pipeline

        case_id = uuid.uuid4()
        db_case = _make_db_case(case_id)
        mock_session_cm = _make_mock_session(db_case)

        published_events: list[PipelineProgressEvent] = []

        async def _capture_publish(event: PipelineProgressEvent) -> None:
            published_events.append(event)

        with (
            patch("src.services.database.async_session", return_value=mock_session_cm),
            patch(
                "src.services.pipeline_events.publish_progress",
                side_effect=_capture_publish,
            ),
            patch(
                "src.pipeline.graph.runner.GraphPipelineRunner.run",
                new_callable=AsyncMock,
                side_effect=RuntimeError("simulated LLM quota exhausted"),
            ),
        ):
            await _run_case_pipeline(case_id)

        assert len(published_events) == 1
        evt = published_events[0]
        assert evt.phase == "failed"
        assert evt.agent == "pipeline"
        assert str(case_id) == str(evt.case_id)
        assert evt.step is None
        assert "simulated LLM quota exhausted" in (evt.error or "")
        assert evt.detail == {"reason": "orchestrator_exception"}

    async def test_failed_frame_published_before_db_flip(self):
        """publish_progress must be called BEFORE the DB commit that flips status."""
        from src.api.routes.cases import _run_case_pipeline

        case_id = uuid.uuid4()
        db_case = _make_db_case(case_id)
        mock_session_cm = _make_mock_session(db_case)

        call_order: list[str] = []

        async def _capture_publish(event: PipelineProgressEvent) -> None:
            call_order.append("publish")

        async def _mock_commit() -> None:
            call_order.append("db_commit")

        mock_session_cm.__aenter__.return_value.commit = _mock_commit

        with (
            patch("src.services.database.async_session", return_value=mock_session_cm),
            patch(
                "src.services.pipeline_events.publish_progress",
                side_effect=_capture_publish,
            ),
            patch(
                "src.pipeline.graph.runner.GraphPipelineRunner.run",
                new_callable=AsyncMock,
                side_effect=ValueError("graph recursion limit exceeded"),
            ),
        ):
            await _run_case_pipeline(case_id)

        assert call_order.index("publish") < call_order.index("db_commit"), (
            f"publish must precede db_commit; got order: {call_order}"
        )

    async def test_db_status_set_to_failed_after_publish(self):
        """DB status must still be flipped even when publish_progress is called first."""
        from src.api.routes.cases import _run_case_pipeline
        from src.models.case import CaseStatus as CaseStatusModel

        case_id = uuid.uuid4()
        db_case = _make_db_case(case_id)
        mock_session_cm = _make_mock_session(db_case)

        with (
            patch("src.services.database.async_session", return_value=mock_session_cm),
            patch(
                "src.services.pipeline_events.publish_progress",
                new_callable=AsyncMock,
            ),
            patch(
                "src.pipeline.graph.runner.GraphPipelineRunner.run",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
        ):
            await _run_case_pipeline(case_id)

        assert db_case.status == CaseStatusModel.failed

    async def test_error_message_truncated_to_500_chars(self):
        """Long exception messages must be truncated to 500 chars in the SSE frame."""
        from src.api.routes.cases import _run_case_pipeline

        case_id = uuid.uuid4()
        db_case = _make_db_case(case_id)
        mock_session_cm = _make_mock_session(db_case)

        long_message = "x" * 1000
        published: list[PipelineProgressEvent] = []

        async def _capture(event: PipelineProgressEvent) -> None:
            published.append(event)

        with (
            patch("src.services.database.async_session", return_value=mock_session_cm),
            patch(
                "src.services.pipeline_events.publish_progress",
                side_effect=_capture,
            ),
            patch(
                "src.pipeline.graph.runner.GraphPipelineRunner.run",
                new_callable=AsyncMock,
                side_effect=RuntimeError(long_message),
            ),
        ):
            await _run_case_pipeline(case_id)

        assert len(published) == 1
        assert len(published[0].error or "") <= 500
