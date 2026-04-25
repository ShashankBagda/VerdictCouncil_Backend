"""Sprint 2 2.A2.2 — CaseState ↔ AsyncPostgresSaver serializer round-trip check.

Why this exists: AsyncPostgresSaver serializes node state through LangGraph's
`JsonPlusSerializer` (msgpack + custom encoders). CaseState carries
tz-aware datetimes, custom Pydantic models, enums, deeply-nested dicts,
and unicode/escape strings — all msgpack drop-zone candidates. A silent
field drop here would corrupt every checkpoint and only surface during a
gate-resume far from the original write. This script catches it before
the cutover.

Constraint (no staging DB available, per 2026-04-25 user decision): the
serializer is exercised in-process; no Postgres connection is required.
The on-disk JSON used by `AsyncPostgresSaver.put` is what `dumps_typed`
produces, so the round-trip semantics here match production exactly.

Usage:
    python scripts/check_casestate_serialization.py [--report-path PATH]

Exit code 0 on full pass; non-zero if any fixture mismatches. The
human-readable report defaults to `tasks/serialization-audit-<date>.md`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Run under strict-msgpack so unallowed types fail loudly and the
# allowlist below is enforced (matches the upcoming LangGraph default).
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from deepdiff import DeepDiff  # noqa: E402
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer  # noqa: E402

from src.shared.case_state import CaseDomainEnum, CaseState, CaseStatusEnum  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "serialization_edge_cases"
DEFAULT_REPORT_DIR = REPO_ROOT.parent / "tasks"

# LangGraph announced strict-msgpack as a future default. Pre-registering
# the project's types here keeps the cutover safe under both modes and
# silences deprecation warnings.
_SERIALIZER = JsonPlusSerializer().with_msgpack_allowlist(
    [CaseState, CaseStatusEnum, CaseDomainEnum]
)


@dataclass
class FixtureResult:
    name: str
    passed: bool
    encoded_bytes: int
    diff: str  # empty when passed


def _check_fixture(path: Path) -> FixtureResult:
    raw = json.loads(path.read_text())
    original = CaseState.model_validate(raw)
    type_name, encoded = _SERIALIZER.dumps_typed(original)
    restored = _SERIALIZER.loads_typed((type_name, encoded))

    diff = DeepDiff(
        original.model_dump(mode="json"),
        restored.model_dump(mode="json"),
        ignore_order=False,
    )
    return FixtureResult(
        name=path.stem,
        passed=not diff,
        encoded_bytes=len(encoded),
        diff="" if not diff else diff.to_json(indent=2),
    )


def _render_report(results: list[FixtureResult], when: datetime) -> str:
    lines: list[str] = [
        "# CaseState serialization audit",
        "",
        f"- Run: {when.isoformat()}",
        f"- Fixtures: `{FIXTURES_DIR.relative_to(REPO_ROOT)}`",
        "- Serializer: `langgraph.checkpoint.serde.jsonplus.JsonPlusSerializer`",
        f"- Total: **{len(results)}** | Passed: **{sum(r.passed for r in results)}** | "
        f"Failed: **{sum(not r.passed for r in results)}**",
        "",
        "## Per-fixture",
        "",
        "| Fixture | Status | Encoded bytes |",
        "| --- | --- | --- |",
    ]
    for r in results:
        lines.append(f"| `{r.name}` | {'✅ pass' if r.passed else '❌ FAIL'} | {r.encoded_bytes} |")

    failures = [r for r in results if not r.passed]
    if failures:
        lines += ["", "## Diffs (failures only)", ""]
        for r in failures:
            lines += [f"### {r.name}", "", "```json", r.diff, "```", ""]
    else:
        lines += [
            "",
            "All fixtures round-trip with no field loss. Safe to proceed with",
            "PostgresSaver cutover (Sprint 2 2.A2 chain).",
            "",
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Override the default report location.",
    )
    args = parser.parse_args()

    if not FIXTURES_DIR.exists():
        print(f"ERROR: fixtures dir missing: {FIXTURES_DIR}", file=sys.stderr)
        return 2

    fixture_paths = sorted(FIXTURES_DIR.glob("*.json"))
    if not fixture_paths:
        print(f"ERROR: no fixtures in {FIXTURES_DIR}", file=sys.stderr)
        return 2

    results = [_check_fixture(p) for p in fixture_paths]
    when = datetime.now(UTC)
    report = _render_report(results, when)

    report_path = args.report_path or (
        DEFAULT_REPORT_DIR / f"serialization-audit-{when.date().isoformat()}.md"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report)

    failures = [r for r in results if not r.passed]
    passed = len(results) - len(failures)
    print(f"Wrote {report_path}")
    print(f"Fixtures: {len(results)} | Passed: {passed} | Failed: {len(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
