"""Sprint 4 4.D3.1 — compare an eval experiment to the baseline.

Reads the per-scorer aggregate stats for two LangSmith experiments and
fails (non-zero exit) if any scorer drops more than ``--threshold``
(default 0.05 = 5 percentage points) below baseline. Prints a markdown
delta table to stdout — the workflow attaches it as a PR comment.

Usage::

    python tests/eval/compare_to_baseline.py \\
        --experiment   verdict-council-eval-<sha>-stub \\
        --baseline     baseline-<old-sha>-stub \\
        --threshold    0.05 \\
        --comment-path delta.md

Exit codes:
    0   no regression detected (or baseline missing in non-strict mode)
    1   at least one scorer regressed beyond threshold
    2   bad invocation / API error
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _experiment_scores(client, name: str) -> dict[str, float]:
    """Return ``{scorer_name: mean_score}`` for the named experiment."""
    rows = list(client.list_examples_for_experiment(experiment_name=name))
    if not rows:
        # Fallback: aggregate evaluation results directly. The two
        # call shapes vary slightly across langsmith client versions.
        runs = list(client.list_runs(project_name=name))
        scores: dict[str, list[float]] = {}
        for run in runs:
            for fb in getattr(run, "feedback_stats", None) or []:
                key = getattr(fb, "key", None) or getattr(fb, "name", None)
                value = getattr(fb, "score", None) or getattr(fb, "value", None)
                if key and isinstance(value, (int, float)):
                    scores.setdefault(key, []).append(float(value))
        return {k: sum(v) / len(v) for k, v in scores.items() if v}

    aggregated: dict[str, list[float]] = {}
    for row in rows:
        feedback = getattr(row, "feedback", None) or row.get("feedback", []) if isinstance(row, dict) else []
        for fb in feedback:
            key = fb.get("key") if isinstance(fb, dict) else getattr(fb, "key", None)
            score = fb.get("score") if isinstance(fb, dict) else getattr(fb, "score", None)
            if key and isinstance(score, (int, float)):
                aggregated.setdefault(key, []).append(float(score))
    return {k: sum(v) / len(v) for k, v in aggregated.items() if v}


def _format_delta_table(
    deltas: dict[str, tuple[float, float, float]],
    threshold: float,
) -> str:
    lines = [
        "| Scorer | Baseline | Current | Delta |",
        "|---|---:|---:|---:|",
    ]
    for scorer, (base, curr, delta) in sorted(deltas.items()):
        marker = "🔴" if -delta > threshold else "✅"
        lines.append(f"| {marker} `{scorer}` | {base:.3f} | {curr:.3f} | {delta:+.3f} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True, help="Current eval experiment name")
    parser.add_argument("--baseline", required=True, help="Baseline eval experiment name")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.05,
        help="Max permitted scorer drop vs baseline (0.05 = 5 percentage points)",
    )
    parser.add_argument(
        "--comment-path",
        default=None,
        help="Write the markdown delta table to this file (for the PR comment step).",
    )
    parser.add_argument(
        "--allow-missing-baseline",
        action="store_true",
        help="Exit 0 (with a warning) if the baseline experiment is missing.",
    )
    args = parser.parse_args()

    if not os.environ.get("LANGSMITH_API_KEY"):
        print("ERROR: LANGSMITH_API_KEY required.", file=sys.stderr)
        return 2

    try:
        from langsmith import Client
    except ImportError:
        print("ERROR: langsmith not installed.", file=sys.stderr)
        return 2

    client = Client()

    try:
        current = _experiment_scores(client, args.experiment)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: could not read experiment {args.experiment!r}: {exc}", file=sys.stderr)
        return 2

    try:
        baseline = _experiment_scores(client, args.baseline)
    except Exception as exc:  # noqa: BLE001
        if args.allow_missing_baseline:
            print(f"WARNING: baseline {args.baseline!r} unavailable: {exc}; skipping gate.")
            return 0
        print(f"ERROR: could not read baseline {args.baseline!r}: {exc}", file=sys.stderr)
        return 2

    deltas: dict[str, tuple[float, float, float]] = {}
    for scorer in sorted(set(current) | set(baseline)):
        base = baseline.get(scorer, 0.0)
        curr = current.get(scorer, 0.0)
        deltas[scorer] = (base, curr, curr - base)

    table = _format_delta_table(deltas, args.threshold)
    print(table)

    if args.comment_path:
        Path(args.comment_path).write_text(
            f"## Eval Δ vs `{args.baseline}`\n\n{table}\n",
            encoding="utf-8",
        )

    regressed = [
        scorer for scorer, (_b, _c, d) in deltas.items() if -d > args.threshold
    ]
    if regressed:
        print(
            f"\n::error::Eval regression on scorers: {', '.join(regressed)} (>{args.threshold:.0%} drop)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
