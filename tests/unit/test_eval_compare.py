"""Sprint 4 4.D3.1 — pure-function tests for compare_to_baseline."""

from __future__ import annotations

from tests.eval.compare_to_baseline import _format_delta_table


def test_format_delta_table_headers_and_rows() -> None:
    deltas = {
        "citation_accuracy": (0.80, 0.78, -0.02),
        "legal_element_coverage": (0.70, 0.60, -0.10),
    }
    out = _format_delta_table(deltas, threshold=0.05)
    assert "| Scorer | Baseline | Current | Delta |" in out
    # 0.10 drop on coverage exceeds 0.05 threshold → red marker
    assert "🔴" in out
    assert "legal_element_coverage" in out
    # 0.02 drop on accuracy is within tolerance → green marker
    assert "✅" in out


def test_format_delta_table_sorts_alphabetically() -> None:
    deltas = {"zeta": (0.5, 0.5, 0.0), "alpha": (0.5, 0.5, 0.0)}
    out = _format_delta_table(deltas, threshold=0.05)
    # alpha row appears before zeta row
    assert out.index("alpha") < out.index("zeta")


def test_threshold_boundary_inclusive() -> None:
    """A drop equal to threshold passes (>, not >=)."""
    deltas = {"x": (1.0, 0.95, -0.05)}  # exactly threshold
    out = _format_delta_table(deltas, threshold=0.05)
    assert "✅" in out
    assert "🔴" not in out


def test_threshold_boundary_just_over_fails() -> None:
    """A drop one ulp beyond threshold trips the marker."""
    deltas = {"x": (1.0, 0.949, -0.051)}
    out = _format_delta_table(deltas, threshold=0.05)
    assert "🔴" in out
