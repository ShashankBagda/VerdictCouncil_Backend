"""Unit tests for `src.db.pipeline_state.load_case_state`.

Covers the version gate: a checkpoint missing `schema_version`, or at a
different version than `CURRENT_SCHEMA_VERSION`, must fail loud rather
than silently round-trip. A CaseState without `schema_version` in the
raw dict would otherwise pydantic-default to 1 and pass through — that
is the exact failure mode this reader is designed to prevent.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from src.db.pipeline_state import (
    CURRENT_SCHEMA_VERSION,
    CheckpointCorruptError,
    CheckpointSchemaMismatchError,
    load_case_state,
    persist_case_state,
)
from src.shared.case_state import CaseState, CaseStatusEnum


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _ReaderSession:
    """AsyncSession-like stub that returns a canned row from execute()."""

    def __init__(self, row: Any = None) -> None:
        self._row = row
        self.executed: list[tuple[Any, dict[str, Any]]] = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.executed.append((statement, params or {}))
        return _FakeResult(self._row)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.asyncio
async def test_load_case_state_returns_none_on_miss():
    session = _ReaderSession(row=None)
    loaded = await load_case_state(session, case_id=uuid4(), run_id="missing")  # type: ignore[arg-type]
    assert loaded is None


@pytest.mark.asyncio
async def test_load_case_state_round_trips_a_valid_row():
    case_id = uuid4()
    original = CaseState(
        case_id=str(case_id),
        run_id="run-1",
        status=CaseStatusEnum.decided,
        verdict_recommendation={"recommendation_type": "compensation", "recommended_outcome": "x"},
    )
    payload = json.loads(original.model_dump_json())

    session = _ReaderSession(row=(payload,))
    loaded = await load_case_state(session, case_id=case_id, run_id="run-1")  # type: ignore[arg-type]

    assert loaded is not None
    assert loaded.case_id == str(case_id)
    assert loaded.run_id == "run-1"
    assert loaded.status == CaseStatusEnum.decided
    assert loaded.verdict_recommendation == {
        "recommendation_type": "compensation",
        "recommended_outcome": "x",
    }


@pytest.mark.asyncio
async def test_load_case_state_accepts_json_string_column():
    """Some drivers hand back JSONB as a str; reader must decode it."""
    case_id = uuid4()
    original = CaseState(case_id=str(case_id), run_id="run-2")
    raw_string = original.model_dump_json()

    session = _ReaderSession(row=(raw_string,))
    loaded = await load_case_state(session, case_id=case_id, run_id="run-2")  # type: ignore[arg-type]

    assert loaded is not None
    assert loaded.run_id == "run-2"


@pytest.mark.asyncio
async def test_load_case_state_rejects_missing_schema_version():
    """A row written before schema_version existed must raise, not default."""
    case_id = uuid4()
    legacy_payload = {
        "case_id": str(case_id),
        "run_id": "legacy",
        "status": "decided",
    }

    session = _ReaderSession(row=(legacy_payload,))
    with pytest.raises(CheckpointCorruptError, match="missing schema_version"):
        await load_case_state(session, case_id=case_id, run_id="legacy")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_load_case_state_rejects_version_mismatch():
    case_id = uuid4()
    future_payload = {
        "schema_version": CURRENT_SCHEMA_VERSION + 99,
        "case_id": str(case_id),
        "run_id": "future",
        "status": "decided",
    }

    session = _ReaderSession(row=(future_payload,))
    with pytest.raises(CheckpointSchemaMismatchError, match="schema_version"):
        await load_case_state(session, case_id=case_id, run_id="future")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_load_case_state_rejects_invalid_json_string():
    case_id = uuid4()
    session = _ReaderSession(row=("{not valid json",))
    with pytest.raises(CheckpointCorruptError, match="not valid JSON"):
        await load_case_state(session, case_id=case_id, run_id="bad")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_load_case_state_rejects_invalid_case_state():
    """A row with schema_version=1 but bad shape fails CaseState validation."""
    case_id = uuid4()
    bad_payload = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "case_id": str(case_id),
        "status": "not-a-valid-enum-value",
    }

    session = _ReaderSession(row=(bad_payload,))
    with pytest.raises(CheckpointCorruptError, match="CaseState validation"):
        await load_case_state(session, case_id=case_id, run_id="bad")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_persist_case_state_emits_schema_version_in_payload():
    """The writer must serialize schema_version so the reader's gate works."""
    case_id = uuid4()
    state = CaseState(case_id=str(case_id), run_id="run-w1")

    captured: dict[str, Any] = {}

    class _WriterSession:
        async def execute(self, statement: Any, params: dict[str, Any]) -> None:
            captured.update(params)

        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

    await persist_case_state(
        _WriterSession(),  # type: ignore[arg-type]
        case_id=case_id,
        run_id="run-w1",
        agent_name="intake",
        state=state,
    )

    decoded = json.loads(captured["state"])
    assert decoded["schema_version"] == CURRENT_SCHEMA_VERSION
