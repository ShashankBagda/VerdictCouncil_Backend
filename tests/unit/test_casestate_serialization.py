"""CaseState round-trip through LangGraph's JsonPlusSerializer (Sprint 2 2.A2.2).

These edge cases are the ones msgpack tends to drop silently:
  - tz-aware datetime in two different zones (UTC, SGT)
  - Custom Pydantic BaseModel instances
  - str/Enum + IntEnum-like values
  - Deeply-nested dict (5+ levels)
  - extra_instructions-style strings: multi-line, unicode, escape characters

Each fixture lives as a JSON file under
`tests/fixtures/serialization_edge_cases/` so the canonical input shape is
visible and reviewable. The test ingests the JSON via Pydantic, round-trips
the resulting CaseState through the same serializer that
`AsyncPostgresSaver` uses, and asserts deep equality. Any silent field drop
fails the test — that's the production failure mode this guards against.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from src.shared.case_state import (
    CaseDomainEnum,
    CaseState,
    CaseStatusEnum,
)

# NOTE: we do not set `LANGGRAPH_STRICT_MSGPACK=true` here because that flag
# changes how langgraph wraps savers (breaks identity assertions in
# `tests/integration/test_runner_checkpointed.py`). Strict mode lives in
# `scripts/check_casestate_serialization.py` instead — the script runs as
# a standalone process so process-scoped env mutations don't bleed into
# the rest of the test suite.

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "serialization_edge_cases"

# LangGraph's future-default strict-msgpack mode requires an explicit
# allowlist for project-defined types. Pre-registering them here also
# silences the deprecation warning during tests.
_SERIALIZER = JsonPlusSerializer().with_msgpack_allowlist(
    [CaseState, CaseStatusEnum, CaseDomainEnum]
)


def _load_fixture(path: Path) -> CaseState:
    return CaseState.model_validate(json.loads(path.read_text()))


def _roundtrip(state: CaseState) -> CaseState:
    encoded = _SERIALIZER.dumps_typed(state)
    restored = _SERIALIZER.loads_typed(encoded)
    assert isinstance(restored, CaseState), f"unexpected restored type: {type(restored)}"
    return restored


def _fixture_paths() -> list[Path]:
    return sorted(FIXTURES_DIR.glob("*.json"))


def test_fixture_directory_contains_edge_cases() -> None:
    paths = _fixture_paths()
    assert len(paths) >= 5, f"expected ≥5 edge-case fixtures, found {len(paths)} in {FIXTURES_DIR}"


@pytest.mark.parametrize("fixture_path", _fixture_paths(), ids=lambda p: p.stem)
def test_casestate_roundtrips_through_jsonplus_serializer(fixture_path: Path) -> None:
    original = _load_fixture(fixture_path)
    restored = _roundtrip(original)

    # Pydantic models compare by all fields, so a missing field surfaces
    # as inequality. dump-equality also catches silent type coercion.
    assert restored == original, (
        f"round-trip mismatch for {fixture_path.name}: "
        f"original={original.model_dump()} restored={restored.model_dump()}"
    )
    assert restored.model_dump(mode="json") == original.model_dump(mode="json")
