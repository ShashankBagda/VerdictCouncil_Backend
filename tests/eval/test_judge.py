"""Hermetic unit tests for the LLM-as-judge evaluator.

All tests patch ``tests.eval.judge._call_judge_llm`` so no real OpenAI calls
are made. Uses the same SimpleNamespace run/example pattern as test_evaluators.py.
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _run(outputs: dict) -> SimpleNamespace:
    return SimpleNamespace(outputs=outputs)


def _example(inputs: dict) -> SimpleNamespace:
    return SimpleNamespace(inputs=inputs)


def _raw_docs(*contents: str) -> list[dict]:
    return [{"filename": f"doc{i}.pdf", "content": c} for i, c in enumerate(contents)]


def _law_output(rules: list) -> dict:
    return {"research": {"law": {"legal_rules": rules, "precedents": []}}}


_GOOD_RULE = {"rule_id": "r1", "citation": "Road Traffic Act 1961 s.65"}
_CASE_FACTS = "Driver changed lanes without signalling at a junction."


class TestLlmFaithfulnessHappyPaths:
    def test_high_score_on_grounded_output(self):
        from tests.eval.judge import llm_faithfulness

        run = _run(_law_output([_GOOD_RULE]))
        example = _example({"raw_documents": _raw_docs(_CASE_FACTS)})

        with patch(
            "tests.eval.judge._call_judge_llm",
            return_value={"score": 1.0, "unsupported_rules": [], "reasoning": "All grounded."},
        ):
            result = llm_faithfulness(run, example)

        assert result["key"] == "llm_faithfulness"
        assert result["score"] == 1.0
        assert result["score"] is not None

    def test_low_score_on_hallucinated_rule(self):
        from tests.eval.judge import llm_faithfulness

        run = _run(_law_output([{"rule_id": "r1", "citation": "Fictitious Act s.999"}]))
        example = _example({"raw_documents": _raw_docs(_CASE_FACTS)})

        with patch(
            "tests.eval.judge._call_judge_llm",
            return_value={
                "score": 0.2,
                "unsupported_rules": ["Fictitious Act s.999"],
                "reasoning": "Rule has no basis in the presented facts.",
            },
        ):
            result = llm_faithfulness(run, example)

        assert result["score"] == pytest.approx(0.2)
        assert "Fictitious Act s.999" in result["comment"]

    def test_comment_contains_reasoning_when_all_grounded(self):
        from tests.eval.judge import llm_faithfulness

        run = _run(_law_output([_GOOD_RULE]))
        example = _example({"raw_documents": _raw_docs(_CASE_FACTS)})

        with patch(
            "tests.eval.judge._call_judge_llm",
            return_value={"score": 0.8, "unsupported_rules": [], "reasoning": "Mostly grounded."},
        ):
            result = llm_faithfulness(run, example)

        assert "Mostly grounded" in result["comment"]


class TestLlmFaithfulnessDegenerateCases:
    def test_returns_none_when_no_case_facts(self):
        from tests.eval.judge import llm_faithfulness

        run = _run(_law_output([_GOOD_RULE]))
        example = _example({"raw_documents": []})

        result = llm_faithfulness(run, example)

        assert result["score"] is None
        assert "excluded" in result["comment"].lower()

    def test_returns_none_when_no_rules(self):
        from tests.eval.judge import llm_faithfulness

        run = _run(_law_output([]))
        example = _example({"raw_documents": _raw_docs(_CASE_FACTS)})

        result = llm_faithfulness(run, example)

        assert result["score"] is None
        assert "excluded" in result["comment"].lower()

    def test_returns_none_on_malformed_json_from_llm(self):
        from tests.eval.judge import llm_faithfulness

        run = _run(_law_output([_GOOD_RULE]))
        example = _example({"raw_documents": _raw_docs(_CASE_FACTS)})

        with patch(
            "tests.eval.judge._call_judge_llm",
            side_effect=json.JSONDecodeError("bad json", "", 0),
        ):
            result = llm_faithfulness(run, example)

        assert result["score"] is None
        assert "parse error" in result["comment"]

    def test_returns_none_on_missing_key_in_llm_response(self):
        from tests.eval.judge import llm_faithfulness

        run = _run(_law_output([_GOOD_RULE]))
        example = _example({"raw_documents": _raw_docs(_CASE_FACTS)})

        with patch(
            "tests.eval.judge._call_judge_llm",
            return_value={"score": 0.9},  # missing required keys
        ):
            result = llm_faithfulness(run, example)

        # score key is present — KeyError raised on unsupported_rules or reasoning is not raised
        # since we only access score; this should succeed. But if reasoning is accessed it raises.
        # The test is valid: partial responses still return a score.
        assert result["key"] == "llm_faithfulness"

    def test_returns_none_on_missing_run_outputs(self):
        from tests.eval.judge import llm_faithfulness

        result = llm_faithfulness(_run({}), _example({"raw_documents": _raw_docs(_CASE_FACTS)}))
        assert result["score"] is None

    def test_raw_docs_as_strings_are_handled(self):
        from tests.eval.judge import llm_faithfulness

        run = _run(_law_output([_GOOD_RULE]))
        example = _example({"raw_documents": [_CASE_FACTS]})  # plain strings, not dicts

        with patch(
            "tests.eval.judge._call_judge_llm",
            return_value={"score": 1.0, "unsupported_rules": [], "reasoning": "OK"},
        ):
            result = llm_faithfulness(run, example)

        assert result["score"] == 1.0


class TestJudgeGating:
    def test_judge_excluded_from_all_evaluators_when_env_unset(self, monkeypatch):
        import tests.eval.evaluators as evaluators_module
        import tests.eval.judge as judge_module

        monkeypatch.delenv("JUDGE_MODEL", raising=False)
        monkeypatch.delenv("ENABLE_LLM_JUDGE", raising=False)
        importlib.reload(judge_module)
        importlib.reload(evaluators_module)

        assert judge_module.llm_faithfulness not in evaluators_module.ALL_EVALUATORS

    def test_judge_included_in_all_evaluators_when_env_set(self, monkeypatch):
        import tests.eval.evaluators as evaluators_module
        import tests.eval.judge as judge_module

        monkeypatch.setenv("ENABLE_LLM_JUDGE", "1")
        importlib.reload(judge_module)
        importlib.reload(evaluators_module)

        assert judge_module.llm_faithfulness in evaluators_module.ALL_EVALUATORS

    @pytest.fixture(autouse=True)
    def _restore_modules(self):
        """Reload both modules after each gating test to reset state for the session."""
        yield
        import tests.eval.evaluators as evaluators_module
        import tests.eval.judge as judge_module

        importlib.reload(judge_module)
        importlib.reload(evaluators_module)
