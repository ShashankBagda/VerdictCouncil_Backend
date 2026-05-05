"""LLM-as-judge evaluator: faithfulness of legal research to case facts.

Scores whether the legal rules identified by the pipeline agent are actually
grounded in the case facts provided to the model. Complements the rule-based
evaluators (citation_accuracy, legal_element_coverage) which only check IDs
and statute references — they cannot detect semantically inapplicable rules.

Gating
------
The judge is disabled by default so default CI runs never hit OpenAI.
Enable it by setting either:

    JUDGE_MODEL=<model-id>          # overrides the model AND enables the judge
    ENABLE_LLM_JUDGE=1              # enables the judge, uses strong-reasoning tier

When disabled, ``JUDGE_ENABLED`` is ``False`` and ``llm_faithfulness`` is
excluded from ``ALL_EVALUATORS`` in evaluators.py at import time.

Usage
-----
    ENABLE_LLM_JUDGE=1 OPENAI_API_KEY=sk-... \\
        python -m tests.eval.run_eval --mode graph --limit 1
"""

from __future__ import annotations

import json
import os
from typing import Any

import openai

from src.shared.config import settings
from tests.eval.evaluators import _agent_law

JUDGE_ENABLED: bool = bool(os.getenv("JUDGE_MODEL")) or os.getenv("ENABLE_LLM_JUDGE") == "1"

_SYSTEM_PROMPT = (
    "You are a legal research auditor. "
    "Given case facts and a list of legal rules identified by an AI agent, "
    "assess whether each rule genuinely applies to the presented facts. "
    "A rule is faithful if it is directly relevant and applicable; "
    "it is unfaithful if it is irrelevant, hallucinated, or misapplied. "
    "Return ONLY valid JSON matching the schema: "
    '{"score": <float 0.0-1.0>, "unsupported_rules": [<rule text>], "reasoning": <string>}. '
    "score=1.0 means all rules are grounded; deduct proportionally for each unsupported rule."
)


def _build_prompt(facts: str, rules_text: str) -> str:
    return (
        f"CASE FACTS:\n{facts}\n\n"
        f"IDENTIFIED LEGAL RULES:\n{rules_text}\n\n"
        "List any rules that are not grounded in the case facts, then provide your score."
    )


def _call_judge_llm(prompt: str) -> dict[str, Any]:
    """Call the judge model and return the parsed JSON result.

    Separated into its own function so unit tests can patch it without
    touching the openai client.
    """
    model = os.getenv("JUDGE_MODEL") or settings.openai_model_strong_reasoning
    client = openai.OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=model,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "faithfulness_verdict",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "score": {"type": "number"},
                        "unsupported_rules": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "reasoning": {"type": "string"},
                    },
                    "required": ["score", "unsupported_rules", "reasoning"],
                    "additionalProperties": False,
                },
            },
        },
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )
    return json.loads(response.choices[0].message.content)


def llm_faithfulness(run: Any, example: Any) -> dict[str, Any]:
    """Score faithfulness of the agent's legal research to the case facts.

    Returns {key: 'llm_faithfulness', score: 0.0–1.0, comment: str}.
    Returns score=None on degenerate input (no facts or no rules) or on
    LLM parse failure — LangSmith excludes None-scored rows from aggregates.

    Follows the same evaluator-as-callable protocol as citation_accuracy
    and legal_element_coverage in evaluators.py.
    """
    inputs = getattr(example, "inputs", None) or {}
    raw_docs = inputs.get("raw_documents") or []
    facts_parts = [
        doc.get("content", "") if isinstance(doc, dict) else str(doc) for doc in raw_docs
    ]
    facts = "\n\n".join(p for p in facts_parts if p).strip()

    if not facts:
        return {
            "key": "llm_faithfulness",
            "score": None,
            "comment": "No case facts available — row excluded from aggregate.",
        }

    law = _agent_law(run)
    rules = law.get("legal_rules") or []
    if not rules:
        return {
            "key": "llm_faithfulness",
            "score": None,
            "comment": "No legal rules in run output — row excluded from aggregate.",
        }

    rules_text = json.dumps(rules, indent=2)
    prompt = _build_prompt(facts, rules_text)

    try:
        result = _call_judge_llm(prompt)
        score = float(result["score"])
        unsupported = result.get("unsupported_rules") or []
        reasoning = result.get("reasoning", "")
        if unsupported:
            comment = f"Unsupported rules: {unsupported!r}. {reasoning}"
        else:
            comment = reasoning or f"All {len(rules)} rule(s) grounded."
        return {"key": "llm_faithfulness", "score": score, "comment": comment}
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        return {
            "key": "llm_faithfulness",
            "score": None,
            "comment": f"judge parse error: {e!r}",
        }
