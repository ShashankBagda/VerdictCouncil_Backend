"""Sprint 4 4.D3.1 — pure-function tests for compare_to_baseline + run_eval guards."""

from __future__ import annotations

import sys

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


def test_added_scorers_surfaced_separately_not_in_regression_check() -> None:
    """A new-in-PR scorer must not appear as a delta row.

    Sprint 3 review finding: zero-fill comparison made ``delta = curr - 0.0``
    for any scorer present only in `current`, which never tripped the gate
    but inflated the markdown summary. Surface added scorers in their own
    section instead so the gate stays comparable like-for-like.
    """
    deltas = {"common": (0.8, 0.78, -0.02)}
    out = _format_delta_table(
        deltas,
        threshold=0.05,
        added=["new_scorer"],
        removed=[],
    )
    assert "common" in out
    # The new scorer is surfaced in its own section, NOT as a delta row
    # with a 0.000 baseline (the previous shape).
    assert "New scorers" in out
    assert "➕" in out
    assert "new_scorer" in out
    # Crucially: no delta row line for new_scorer.
    new_scorer_lines = [ln for ln in out.splitlines() if "new_scorer" in ln and "|" in ln]
    assert new_scorer_lines == []


def test_removed_scorers_surfaced_separately_not_treated_as_regression() -> None:
    """A scorer removed in this PR must not falsely trip the regression gate.

    Pre-fix behaviour: ``delta = 0.0 - base`` always exceeded threshold,
    so any PR that removed a scorer flagged a fake regression.
    """
    deltas = {"common": (0.8, 0.81, 0.01)}
    out = _format_delta_table(
        deltas,
        threshold=0.05,
        added=[],
        removed=["dropped_scorer"],
    )
    assert "Scorers removed" in out
    assert "➖" in out
    assert "dropped_scorer" in out
    # Removed scorer must not appear as a delta row with a 0.000 current.
    dropped_lines = [ln for ln in out.splitlines() if "dropped_scorer" in ln and "0.000" in ln]
    assert dropped_lines == []


# ---------------------------------------------------------------------------
# run_eval baseline-tag guard
# ---------------------------------------------------------------------------


def test_run_eval_refuses_baseline_tag_with_stub_mode(monkeypatch, capsys) -> None:
    """Sprint 3 review finding: stub-mode is engineered to score 1.0/1.0,
    so tagging a stub experiment as the baseline produces a meaningless
    regression floor. The guard rejects any baseline-* prefix unless
    --mode graph.
    """
    from tests.eval import run_eval

    monkeypatch.setenv("LANGSMITH_API_KEY", "test")
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_eval.py", "--mode", "stub", "--experiment-prefix", "baseline-abc123"],
    )

    rc = run_eval.main()

    captured = capsys.readouterr()
    assert rc == 2
    assert "baseline" in captured.err
    assert "--mode graph" in captured.err


def test_run_eval_refuses_baseline_tag_with_failing_stub_mode(monkeypatch, capsys) -> None:
    """The guard also fires on `--mode failing-stub` (a stub variant)."""
    from tests.eval import run_eval

    monkeypatch.setenv("LANGSMITH_API_KEY", "test")
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_eval.py", "--mode", "failing-stub", "--experiment-prefix", "baseline-abc123"],
    )

    rc = run_eval.main()

    captured = capsys.readouterr()
    assert rc == 2
    assert "baseline" in captured.err
