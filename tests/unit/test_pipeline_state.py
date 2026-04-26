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
    SUPPORTED_READ_SCHEMA_VERSIONS,
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
        status=CaseStatusEnum.ready_for_review,
        hearing_analysis={
            "preliminary_conclusion": "Balance of evidence favours claimant.",
            "confidence_score": 70,
        },
    )
    payload = json.loads(original.model_dump_json())

    session = _ReaderSession(row=(payload,))
    loaded = await load_case_state(session, case_id=case_id, run_id="run-1")  # type: ignore[arg-type]

    assert loaded is not None
    assert loaded.case_id == str(case_id)
    assert loaded.run_id == "run-1"
    assert loaded.status == CaseStatusEnum.ready_for_review
    assert loaded.hearing_analysis.preliminary_conclusion == "Balance of evidence favours claimant."
    assert loaded.hearing_analysis.confidence_score == 70


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
async def test_load_case_state_rejects_unsupported_version_explicitly():
    """Q2.3a: the reader's accept set is {2, 3} — anything outside (here
    99) is rejected. Locks the intent in test names so a future change
    accidentally widening the set is caught."""
    case_id = uuid4()
    payload = {
        "schema_version": 99,
        "case_id": str(case_id),
        "run_id": "v99",
        "status": "pending",
    }

    session = _ReaderSession(row=(payload,))
    with pytest.raises(CheckpointSchemaMismatchError):
        await load_case_state(session, case_id=case_id, run_id="v99")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_load_case_state_accepts_v3_with_intake_extraction():
    """Q2.3a: a v3 checkpoint synthesised in test round-trips —
    schema_version stays 3, intake_extraction survives."""
    case_id = uuid4()
    v3_payload = {
        "schema_version": 3,
        "case_id": str(case_id),
        "run_id": "v3",
        "status": "pending",
        "intake_extraction": {"fields": {"parties": [{"name": "Alice"}]}},
    }

    session = _ReaderSession(row=(v3_payload,))
    loaded = await load_case_state(session, case_id=case_id, run_id="v3")  # type: ignore[arg-type]

    assert loaded is not None
    assert loaded.schema_version == 3
    assert loaded.intake_extraction == {"fields": {"parties": [{"name": "Alice"}]}}


@pytest.mark.asyncio
async def test_load_case_state_v2_defaults_intake_extraction_to_none():
    """Q2.3a: a v2 checkpoint (writer hasn't flipped yet) loads with
    intake_extraction defaulted to None — old runs aren't broken by
    the model gaining the field."""
    case_id = uuid4()
    v2_payload = {
        "schema_version": 2,
        "case_id": str(case_id),
        "run_id": "v2",
        "status": "pending",
    }

    session = _ReaderSession(row=(v2_payload,))
    loaded = await load_case_state(session, case_id=case_id, run_id="v2")  # type: ignore[arg-type]

    assert loaded is not None
    assert loaded.schema_version == 2
    assert loaded.intake_extraction is None


@pytest.mark.asyncio
async def test_v3_checkpoint_round_trips_through_existing_writer():
    """Q2.3a: a v3 checkpoint loaded → re-serialised by the unchanged
    writer → loads cleanly. The writer doesn't override
    `schema_version` (it serializes the field as-is via
    `model_dump_json`), so a v3 row stays v3 across a load → persist
    cycle and `intake_extraction` is preserved. This is the property
    that makes Q2.3a safe to ship before Q2.3b: any in-flight v3 data
    survives unchanged, even though the model's *default* is still 2."""
    case_id = uuid4()
    v3_payload = {
        "schema_version": 3,
        "case_id": str(case_id),
        "run_id": "round-trip",
        "status": "pending",
        "intake_extraction": {"fields": {"parties": [{"name": "Bob"}]}},
    }

    reader = _ReaderSession(row=(v3_payload,))
    loaded = await load_case_state(reader, case_id=case_id, run_id="round-trip")  # type: ignore[arg-type]
    assert loaded is not None

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
        run_id="round-trip",
        agent_name="intake",
        state=loaded,
    )

    decoded = json.loads(captured["state"])
    assert decoded["schema_version"] == 3  # in-memory field preserved
    assert decoded["intake_extraction"] == {"fields": {"parties": [{"name": "Bob"}]}}

    re_reader = _ReaderSession(row=(decoded,))
    reloaded = await load_case_state(re_reader, case_id=case_id, run_id="round-trip")  # type: ignore[arg-type]
    assert reloaded is not None
    assert reloaded.schema_version == 3
    assert reloaded.intake_extraction == {"fields": {"parties": [{"name": "Bob"}]}}


@pytest.mark.asyncio
async def test_new_state_constructed_today_still_stamps_v2():
    """Q2.3a: the model's default for `schema_version` is unchanged at
    2, so any *new* CaseState the runner builds today still serializes
    as a v2 row — Q2.3b is the writer flip."""
    state = CaseState()
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
        case_id=uuid4(),
        run_id="new",
        agent_name="intake",
        state=state,
    )

    decoded = json.loads(captured["state"])
    assert decoded["schema_version"] == CURRENT_SCHEMA_VERSION
    assert CURRENT_SCHEMA_VERSION == 2  # locked in until Q2.3b


def test_supported_read_schema_versions_is_v2_and_v3():
    """Lock the reader-accept set so a code change that drops v2
    breaks this test loudly (rather than silently breaking in-flight
    runs that started pre-bake)."""
    assert frozenset({2, 3}) == SUPPORTED_READ_SCHEMA_VERSIONS


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
