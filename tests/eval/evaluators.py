"""Sprint 3 3.D1.2 — custom LangSmith evaluators.

Two evaluators score VerdictCouncil pipeline runs against the golden
dataset uploaded by ``dataset_sync.py``:

- :class:`CitationAccuracy` — every cited ``source_id`` in the agent's
  ``LawResearch`` appears in the run's tool-artifact chain. Catches
  hallucinated citations the validator (3.B.5) failed to suppress.

- :class:`LegalElementCoverage` — every statutory citation listed in
  ``expected.research.legal_rules`` is addressed in the agent's
  ``legal_rules`` output (substring match on the statute identifier).
  Measures whether the law subagent found the rules the case calls for.

Both follow LangSmith's evaluator-as-callable protocol: invoked with
``(run, example)`` and return a dict the SDK coerces into an
:class:`~langsmith.evaluation.EvaluationResult`. Each evaluator is
also a plain function so it's trivial to unit-test without a Run.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Output extraction
# ---------------------------------------------------------------------------


def _law_block(outputs: dict[str, Any] | None) -> dict[str, Any]:
    """Pull the law sub-output out of either the agent's structured response
    or the dataset's expected dict — both share the
    ``research.law`` shape."""
    if not outputs:
        return {}
    research = outputs.get("research") or outputs.get("research_output") or {}
    if isinstance(research, dict):
        return research.get("law") or research
    return {}


def _retrieved_source_ids(run: Any) -> set[str]:
    """Collect source_ids from a run's tool-artifact chain.

    Two surfaces, in priority order:
    1. ``run.outputs["retrieved_source_ids"]`` — populated by the agent
       factory's source-id extractor (Sprint 3 3.B.5 wiring).
    2. ``run.outputs["audit"]["source_ids"]`` — the audit middleware's
       JSONB stash (3.B.3).
    Falls back to an empty set so a missing surface scores 0, not crash.
    """
    if run is None:
        return set()
    outputs = getattr(run, "outputs", None) or {}
    explicit = outputs.get("retrieved_source_ids")
    if explicit:
        return set(explicit)
    audit = outputs.get("audit") or {}
    return set(audit.get("source_ids") or [])


def _agent_law(run: Any) -> dict[str, Any]:
    if run is None:
        return {}
    return _law_block(getattr(run, "outputs", None) or {})


def _expected_law(example: Any) -> dict[str, Any]:
    if example is None:
        return {}
    return _law_block(getattr(example, "outputs", None) or {})


# ---------------------------------------------------------------------------
# Evaluators
# ---------------------------------------------------------------------------


def citation_accuracy(run: Any, example: Any | None = None) -> dict[str, Any]:
    """Fraction of cited source_ids that match the run's retrieved set.

    1.0 = every citation is grounded; 0.0 = every citation is hallucinated.

    Empty law block (no citations emitted) returns ``score=None`` so
    LangSmith excludes the row from the accuracy aggregate. Returning
    1.0 here was gameable: the validator suppressing hallucinated
    citations would *raise* the score, and an agent that learned to
    emit zero rules would earn a perfect anti-hallucination grade
    (Sprint 3 review finding). The companion ``legal_element_coverage``
    metric still flags zero-rules cases as missed coverage, so quality
    can't degrade silently — this fix only stops empty rows from
    inflating the precision metric.
    """
    law = _agent_law(run)
    rules = law.get("legal_rules") or []
    precedents = law.get("precedents") or []
    cited: list[str] = []
    for item in (*rules, *precedents):
        if isinstance(item, dict):
            cited.extend(item.get("supporting_sources") or [])

    retrieved = _retrieved_source_ids(run)
    if not cited:
        return {
            "key": "citation_accuracy",
            "score": None,
            "comment": "No citations to validate — row excluded from aggregate.",
        }

    matched = sum(1 for src in cited if src in retrieved)
    score = matched / len(cited)
    comment = f"{matched}/{len(cited)} cited source_ids found in tool-artifact chain"
    return {"key": "citation_accuracy", "score": score, "comment": comment}


# Match section/schedule/part references with explicit word-boundary
# anchoring. The longer alternatives MUST come first because Python's
# regex engine takes the leftmost alternative that matches — without
# this, `s` would shadow `section`/`schedule` and `s.65` would never
# resolve to `s65`.
_SECTION_RE = re.compile(
    r"\b(section|schedule|sch|part|pt|s)\b\.?\s*([0-9ivxlcdm]+)",
    re.IGNORECASE,
)


def _statute_section_keys(text: str) -> set[str]:
    """Extract section/schedule identifiers from a citation string.

    'Road Traffic Act 1961 s.65 (improper lane change)' → {'s65'}
    'Section 65'                                        → {'s65'}
    'CPFTA Part III'                                    → {'partiii'}
    'Schedule 9' / 'Sch. 9'                             → {'schedule9'}

    These are the load-bearing tokens for cross-form matching: the
    agent may write 'Section 65' while the expected says 's.65', and
    the gate must still see them as the same provision.
    """
    canonical = {
        "section": "s",
        "s": "s",
        "schedule": "schedule",
        "sch": "schedule",
        "part": "part",
        "pt": "part",
    }
    keys: set[str] = set()
    for match in _SECTION_RE.finditer(text):
        prefix = canonical[match.group(1).lower()]
        keys.add(f"{prefix}{match.group(2).lower()}")
    return keys


def legal_element_coverage(run: Any, example: Any) -> dict[str, Any]:
    """Fraction of expected legal_rule statutes that appear in the run.

    Matches on extracted section identifiers (``s65``, ``schedule9``,
    ``partiii``) — robust to the agent paraphrasing the act's full
    name while still requiring it to land on the right provision.
    """
    expected = _expected_law(example)
    expected_rules = expected.get("legal_rules") or []
    if not expected_rules:
        return {
            "key": "legal_element_coverage",
            "score": 1.0,
            "comment": "No expected rules in this example.",
        }

    actual_keys: set[str] = set()
    for rule in _agent_law(run).get("legal_rules") or []:
        if isinstance(rule, dict):
            for field in ("citation", "rule_id", "text"):
                if rule.get(field):
                    actual_keys |= _statute_section_keys(str(rule[field]))

    matched: list[str] = []
    missed: list[str] = []
    for expected_rule in expected_rules:
        expected_keys = _statute_section_keys(str(expected_rule))
        if expected_keys and expected_keys & actual_keys:
            matched.append(expected_rule)
        else:
            missed.append(expected_rule)

    score = len(matched) / len(expected_rules)
    comment = (
        f"covered {len(matched)}/{len(expected_rules)}: missing {missed!r}"
        if missed
        else f"covered {len(matched)}/{len(expected_rules)}"
    )
    return {"key": "legal_element_coverage", "score": score, "comment": comment}


# Public bundle for run_eval.py (3.D1.3).
ALL_EVALUATORS: tuple = (citation_accuracy, legal_element_coverage)
