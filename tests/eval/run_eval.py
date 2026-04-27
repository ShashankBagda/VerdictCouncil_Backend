"""Sprint 3 3.D1.3 — run the LangSmith eval against the golden dataset.

Wires :mod:`tests.eval.evaluators` into ``langsmith.evaluate`` and
records the experiment in the LangSmith UI.

Three pipeline modes:

- ``--mode stub`` (default): the adapter returns a synthesised output
  derived from the example's expected payload. Verifies the
  dataset + evaluator + LangSmith plumbing without spending OpenAI
  tokens. **The stub is engineered to score 1.0/1.0** — useful for
  proving the gate is wired, not for catching regressions.

- ``--mode failing-stub``: deliberately mis-aligns the synthesised
  output (no source_ids, no rules) so the evaluators score 0. Used to
  prove the 4.D3.1 CI gate can actually fail. Run this once during
  gate setup; do not tag as baseline.

- ``--mode graph`` (real): the adapter compiles the in-process graph
  with :class:`InMemorySaver`, marshals the full example payload into
  ``CaseState``, invokes ``graph.ainvoke``, and returns the
  ``research_output`` + ``retrieved_source_ids`` slots. Requires
  ``OPENAI_API_KEY`` and incurs token cost per example. ``--limit N``
  caps how many examples run. Errors are raised — not logged — so a
  flaky LLM call cannot publish a green experiment with missing rows.

Run::

    LANGSMITH_API_KEY=... uv run python tests/eval/run_eval.py
    LANGSMITH_API_KEY=... uv run python tests/eval/run_eval.py --mode failing-stub
    LANGSMITH_API_KEY=... OPENAI_API_KEY=... \\
        uv run python tests/eval/run_eval.py --mode graph --limit 1

The experiment is named ``<prefix>-<git-sha>-<mode>`` so 3.D1.4 can
tag the baseline + 4.D3.1 can compare against it in CI.

**Limitations of the stub baseline:** ``baseline-<sha>-stub`` proves
the gate is wired, not that quality stayed flat. Until the goldens'
``placeholder-*`` source_ids are reconciled against real OpenAI file
ids and ``--mode graph`` is promoted to the gate, a regression in the
real pipeline cannot trip the stub baseline.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
load_dotenv(REPO_ROOT / ".env", override=False)

DATASET_NAME = "verdict-council-golden"


# ---------------------------------------------------------------------------
# Pipeline adapters
# ---------------------------------------------------------------------------


def stub_adapter(inputs: dict[str, Any]) -> dict[str, Any]:
    """Synthesised output for plumbing-only runs.

    Returns enough structure that the evaluators score 1.0/1.0:
    each input echoes back the expected research with one synthetic
    source_id so ``citation_accuracy`` can be exercised without
    spending tokens. **No discrimination power** — engineered to pass.
    """
    expected_law = (inputs.get("expected") or {}).get("research") or {}
    rules = [
        {"rule_id": f"stub-{i}", "citation": cite, "supporting_sources": ["stub:000"]}
        for i, cite in enumerate(expected_law.get("legal_rules") or [])
    ]
    return {
        "research": {"law": {"legal_rules": rules, "precedents": []}},
        "retrieved_source_ids": {"law": ["stub:000"]},
    }


def failing_stub_adapter(_inputs: dict[str, Any]) -> dict[str, Any]:
    """Deliberately mis-aligned output that scores 0 on both evaluators.

    Used once during 4.D3.1 gate setup to prove the gate can trip.
    Do NOT tag the resulting experiment as the baseline.
    """
    return {
        "research": {"law": {"legal_rules": [], "precedents": []}},
        "retrieved_source_ids": {},
    }


async def graph_adapter(inputs: dict[str, Any]) -> dict[str, Any]:
    """Real adapter: compile in-process graph, run one case, extract outputs.

    Marshals the full example payload (parties / case_metadata /
    raw_documents) into the initial CaseState so intake actually has
    something to chew on. Earlier versions only seeded `case_id` +
    `domain`, which made `--mode graph` a no-op for evaluator scoring.
    """
    import uuid

    from langgraph.checkpoint.memory import InMemorySaver

    from src.pipeline.graph.builder import build_graph
    from src.shared.case_state import CaseState

    saver = InMemorySaver()
    graph = build_graph(checkpointer=saver)

    case_id = inputs.get("case_id") or str(uuid.uuid4())
    case = CaseState(
        case_id=case_id,
        domain=inputs.get("domain", "small_claims"),
        parties=list(inputs.get("parties") or []),
        case_metadata=dict(inputs.get("case_metadata") or {}),
        raw_documents=list(inputs.get("raw_documents") or []),
    )

    initial = {
        "case": case,
        "run_id": f"eval-{case_id}",
        "extra_instructions": {},
        "retry_counts": {},
        "halt": None,
        "research_parts": {},
        "research_output": None,
        "retrieved_source_ids": {},
        "intake_output": None,
        "synthesis_output": None,
        "audit_output": None,
        "pending_action": None,
        "is_resume": False,
        "start_agent": None,
    }
    config = {"configurable": {"thread_id": case_id}}
    result = await graph.ainvoke(initial, config=config)

    research = result.get("research_output")
    research_dict = research.model_dump() if research is not None else None
    return {
        "research": {"law": (research_dict or {}).get("law")},
        "retrieved_source_ids": result.get("retrieved_source_ids") or {},
    }


# ---------------------------------------------------------------------------
# Experiment plumbing
# ---------------------------------------------------------------------------


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True
        )
        return out.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _resolve_target(mode: str):
    """Return a sync callable LangSmith can invoke per example."""
    if mode == "stub":

        def _target(inputs: dict[str, Any]) -> dict[str, Any]:
            return stub_adapter(inputs)

        return _target

    if mode == "failing-stub":

        def _target(inputs: dict[str, Any]) -> dict[str, Any]:
            return failing_stub_adapter(inputs)

        return _target

    import asyncio

    def _target(inputs: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(graph_adapter(inputs))

    return _target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("stub", "failing-stub", "graph"),
        default="stub",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max examples to score")
    parser.add_argument(
        "--experiment-prefix",
        default="verdict-council-eval",
        help="Experiment name prefix; the git short-sha is appended.",
    )
    args = parser.parse_args()

    if not os.environ.get("LANGSMITH_API_KEY"):
        print(
            "ERROR: LANGSMITH_API_KEY is not set. Add it to .env or export it.",
            file=sys.stderr,
        )
        return 2
    if args.mode == "graph" and not os.environ.get("OPENAI_API_KEY"):
        print(
            "ERROR: --mode graph requires OPENAI_API_KEY (real LLM calls).",
            file=sys.stderr,
        )
        return 2

    # 4.D3.1 floor guard — Sprint 3 review finding.
    # `baseline-<sha>-stub` is engineered to score 1.0/1.0 and so cannot
    # trip on real pipeline regressions. Refuse to tag any stub-mode
    # experiment with a baseline-* prefix; the eval-gate floor must be
    # set against `--mode graph` to have discrimination power.
    if args.experiment_prefix.startswith("baseline") and args.mode != "graph":
        print(
            f"ERROR: --experiment-prefix={args.experiment_prefix!r} requires --mode graph. "
            "Stub experiments cannot serve as the regression baseline because they are "
            "engineered to score 1.0/1.0 (see run_eval.py docstring).",
            file=sys.stderr,
        )
        return 2

    from langsmith import Client
    from langsmith.evaluation import evaluate

    from tests.eval.evaluators import citation_accuracy, legal_element_coverage

    target = _resolve_target(args.mode)
    experiment_name = f"{args.experiment_prefix}-{_git_sha()}-{args.mode}"

    client = Client()
    try:
        client.read_dataset(dataset_name=DATASET_NAME)
    except Exception:  # noqa: BLE001
        print(
            f"ERROR: dataset {DATASET_NAME!r} not found. Run dataset_sync.py first.",
            file=sys.stderr,
        )
        return 1

    if args.limit is not None:
        examples = list(client.list_examples(dataset_name=DATASET_NAME, limit=args.limit))
        data: Any = examples
    else:
        data = DATASET_NAME

    # Graph mode hits live LLMs; surface errors loudly so a flaky run
    # cannot ship a green experiment with missing rows. Stub mode keeps
    # the default `log` so a malformed fixture doesn't break the gate's
    # plumbing smoke.
    error_handling = "raise" if args.mode == "graph" else "log"

    results = evaluate(
        target,
        data=data,
        evaluators=[citation_accuracy, legal_element_coverage],
        experiment_prefix=experiment_name,
        max_concurrency=2 if args.mode == "graph" else 5,
        client=client,
        error_handling=error_handling,
    )

    summary_url = getattr(results, "experiment_url", None) or getattr(results, "url", None)
    print(f"Experiment: {experiment_name}")
    if summary_url:
        print(f"URL:        {summary_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
