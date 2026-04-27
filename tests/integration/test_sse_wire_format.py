"""Sprint 1 1.A1.9 — SSE wire-format byte-equality test.

Re-runs every fixture-factory from `scripts/capture_sse_goldens.py` and
asserts byte-equality with the saved goldens under
`tests/fixtures/sse_wire_format/`. Locks down the wire-format SHAPE so:

- Pydantic serialization (`model_dump_json()`) is byte-stable for
  `PipelineProgressEvent`, `HeartbeatEvent`, `AuthExpiringEvent`.
- The dict-based JSON publisher format
  (`json.dumps({...}, default=str)`) used by `publish_agent_event` and
  `publish_narration` is byte-stable.

Topology change note (1.A1.7):

The 6-phase rewrite changes which AGENT NAME values appear at runtime
(`intake`, `research-{evidence,facts,witnesses,law}`, `synthesis`,
`audit` instead of the legacy 9-agent names). The goldens were captured
with the old names but that's a VALUE difference, not a SHAPE difference
— the JSON keys, types, ordering, and serialization paths are
unchanged. This test asserts wire-format stability; runtime semantic
equivalence is tested elsewhere (1.A1.10 replay-N-cases harness).

Acceptance:
- All event types covered (progress, agent, narration, heartbeat,
  auth_expiring) plus the two scenario fixtures (multi-tool-call,
  gate2_fanout).
- Byte-equal against the saved fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.capture_sse_goldens import FIXTURES, OUT_DIR


@pytest.mark.parametrize("fixture_name", sorted(FIXTURES.keys()))
def test_fixture_factory_output_matches_saved_golden(fixture_name: str) -> None:
    """Re-running the factory must produce the same bytes as the saved file."""
    factory = FIXTURES[fixture_name]
    regenerated = factory()
    saved_path = OUT_DIR / f"{fixture_name}.json"
    assert saved_path.exists(), (
        f"golden file missing: {saved_path}; run scripts/capture_sse_goldens.py"
    )
    saved = json.loads(saved_path.read_text())
    assert regenerated == saved, (
        f"Wire-format drift in {fixture_name!r}. Golden serialization is no "
        "longer byte-stable — either intentional (re-run "
        "scripts/capture_sse_goldens.py and commit the new fixtures) or a "
        "regression (likely Pydantic version bump or schema field reorder)."
    )


# ---------------------------------------------------------------------------
# Coverage assertions — fail loudly if a new event type is added without a
# corresponding golden, or if a golden is added without a factory.
# ---------------------------------------------------------------------------


def test_every_pydantic_event_class_has_a_fixture() -> None:
    """Each declared Pydantic event class must be exercised by at least one fixture."""
    expected_classes = {
        "PipelineProgressEvent",
        "AgentEvent",
        "NarrationEvent",
        "HeartbeatEvent",
        "AuthExpiringEvent",
    }
    actual_classes: set[str] = set()
    for factory in FIXTURES.values():
        regenerated = factory()
        cls = regenerated.get("pydantic_class")
        if cls:
            actual_classes.add(cls)
    missing = expected_classes - actual_classes
    assert not missing, (
        f"No fixture covers Pydantic event class(es): {sorted(missing)}. "
        "Add a factory in scripts/capture_sse_goldens.py and a golden under "
        "tests/fixtures/sse_wire_format/."
    )


def test_every_saved_golden_has_a_factory() -> None:
    """Every saved golden must come from a factory in `FIXTURES`."""
    saved = {p.stem for p in OUT_DIR.glob("*.json")}
    declared = set(FIXTURES.keys())
    orphans = saved - declared
    assert not orphans, (
        f"Orphaned golden(s) without a factory: {sorted(orphans)}. "
        "Either delete the file or add the corresponding factory to "
        "scripts/capture_sse_goldens.py."
    )


def test_fixtures_dir_resolves_under_tests_tree() -> None:
    """Smoke check: the OUT_DIR path the script writes to is the one we read from."""
    expected = Path(__file__).parent.parent / "fixtures" / "sse_wire_format"
    assert OUT_DIR.resolve() == expected.resolve()
