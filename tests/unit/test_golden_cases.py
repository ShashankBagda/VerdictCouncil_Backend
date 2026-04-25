"""Sprint 0 0.11b — golden case fixture validation.

Sanity guards for the LangSmith-bound dataset under
`tests/eval/data/golden_cases/`. These keep the dataset's structural
invariants stable so 3.D1.1 (sync) and 3.D1.2 (evaluators) can rely
on them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "eval" / "data" / "golden_cases"

CASES = sorted(p for p in GOLDEN_DIR.glob("*.json"))


def test_count_in_target_band():
    """Spec calls for 10–20 fixtures total (5–10 per domain)."""
    assert 10 <= len(CASES) <= 20, f"expected 10–20 fixtures, found {len(CASES)}"


def test_split_per_domain_in_band():
    by_domain: dict[str, int] = {}
    for path in CASES:
        with path.open() as f:
            doc = json.load(f)
        by_domain[doc["metadata"]["domain"]] = by_domain.get(doc["metadata"]["domain"], 0) + 1
    for domain, count in by_domain.items():
        assert 5 <= count <= 10, f"{domain} has {count} fixtures; expected 5–10"


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_fixture_shape(path: Path):
    with path.open() as f:
        doc = json.load(f)

    assert {"metadata", "inputs", "expected"} <= doc.keys(), (
        f"{path.name} is missing top-level keys"
    )

    meta = doc["metadata"]
    for key in ("id", "domain", "author", "date"):
        assert meta.get(key), f"{path.name} metadata missing {key!r}"
    assert meta["domain"] in {"small_claims", "traffic_violation"}, (
        f"{path.name} has unexpected domain {meta['domain']!r}"
    )

    inputs = doc["inputs"]
    for key in ("case_id", "domain", "parties", "case_metadata", "raw_documents"):
        assert key in inputs, f"{path.name} inputs missing {key!r}"
    assert inputs["domain"] == meta["domain"]
    assert len(inputs["parties"]) >= 2
    assert len(inputs["raw_documents"]) >= 1

    expected = doc["expected"]
    assert "intake" in expected and "research" in expected, (
        f"{path.name} expected must cover at least intake + research"
    )
    research = expected["research"]
    for key in ("legal_rules", "precedents", "supporting_sources"):
        assert key in research, f"{path.name} expected.research missing {key!r}"


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_supporting_sources_format(path: Path):
    """Every supporting_source is `<file_id>:<sha256[:12]>` shape."""
    with path.open() as f:
        doc = json.load(f)
    for sid in doc["expected"]["research"]["supporting_sources"]:
        assert ":" in sid, f"{path.name}: source_id {sid!r} missing colon"
        prefix, suffix = sid.split(":", 1)
        assert prefix, f"{path.name}: empty file_id in {sid!r}"
        assert len(suffix) == 12, f"{path.name}: source_id {sid!r} suffix not 12 chars"
