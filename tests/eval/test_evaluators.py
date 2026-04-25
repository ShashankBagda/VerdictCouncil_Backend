"""Sprint 3 3.D1.2 — evaluator unit tests.

Hermetic: builds bare ``Run`` / ``Example`` look-alikes (just need the
``outputs`` attribute) and asserts every evaluator returns the right
score + key + comment shape on pass + fail paths.
"""

from __future__ import annotations

from types import SimpleNamespace

from tests.eval.evaluators import citation_accuracy, legal_element_coverage


def _run(outputs: dict) -> SimpleNamespace:
    return SimpleNamespace(outputs=outputs)


def _example(outputs: dict) -> SimpleNamespace:
    return SimpleNamespace(outputs=outputs)


# ---------------------------------------------------------------------------
# citation_accuracy
# ---------------------------------------------------------------------------


class TestCitationAccuracy:
    def test_perfect_score_when_every_citation_grounded(self):
        run = _run(
            {
                "research": {
                    "law": {
                        "legal_rules": [
                            {"rule_id": "r-1", "supporting_sources": ["f:a", "f:b"]},
                        ],
                        "precedents": [
                            {"case_name": "X", "supporting_sources": ["f:c"]},
                        ],
                    }
                },
                "retrieved_source_ids": ["f:a", "f:b", "f:c"],
            }
        )
        result = citation_accuracy(run, None)
        assert result["key"] == "citation_accuracy"
        assert result["score"] == 1.0

    def test_zero_when_all_citations_hallucinated(self):
        run = _run(
            {
                "research": {
                    "law": {"legal_rules": [{"rule_id": "r", "supporting_sources": ["fake:1"]}]}
                },
                "retrieved_source_ids": ["real:1"],
            }
        )
        result = citation_accuracy(run, None)
        assert result["score"] == 0.0

    def test_fractional_score_on_partial_match(self):
        run = _run(
            {
                "research": {
                    "law": {
                        "legal_rules": [
                            {"rule_id": "r-1", "supporting_sources": ["f:1"]},
                            {"rule_id": "r-2", "supporting_sources": ["f:fake"]},
                        ]
                    }
                },
                "retrieved_source_ids": ["f:1"],
            }
        )
        result = citation_accuracy(run, None)
        assert result["score"] == 0.5
        assert "1/2" in result["comment"]

    def test_no_citations_scores_one(self):
        run = _run({"research": {"law": {"legal_rules": [], "precedents": []}}})
        assert citation_accuracy(run, None)["score"] == 1.0

    def test_falls_back_to_audit_source_ids(self):
        run = _run(
            {
                "research": {
                    "law": {"legal_rules": [{"rule_id": "r", "supporting_sources": ["f:1"]}]}
                },
                "audit": {"source_ids": ["f:1"]},
            }
        )
        assert citation_accuracy(run, None)["score"] == 1.0

    def test_no_run_outputs_does_not_crash(self):
        result = citation_accuracy(_run({}), None)
        assert result["score"] == 1.0


# ---------------------------------------------------------------------------
# legal_element_coverage
# ---------------------------------------------------------------------------


class TestLegalElementCoverage:
    def test_perfect_score_when_every_expected_rule_present(self):
        run = _run(
            {
                "research": {
                    "law": {
                        "legal_rules": [
                            {"citation": "Road Traffic Act 1961 s.65"},
                            {"citation": "Sale of Goods Act 1979 s.13"},
                        ]
                    }
                }
            }
        )
        example = _example(
            {
                "research": {
                    "legal_rules": [
                        "Road Traffic Act 1961 s.65 (improper lane change)",
                        "Sale of Goods Act 1979 s.13",
                    ]
                }
            }
        )
        result = legal_element_coverage(run, example)
        assert result["key"] == "legal_element_coverage"
        assert result["score"] == 1.0

    def test_partial_score_on_partial_coverage(self):
        run = _run({"research": {"law": {"legal_rules": [{"citation": "RTA 1961 s.65"}]}}})
        example = _example(
            {
                "research": {
                    "legal_rules": [
                        "Road Traffic Act 1961 s.65",
                        "Road Traffic Act 1961 Schedule 9",
                    ]
                }
            }
        )
        result = legal_element_coverage(run, example)
        assert result["score"] == 0.5
        assert "Schedule 9" in result["comment"]

    def test_zero_when_no_expected_rules_match(self):
        run = _run({"research": {"law": {"legal_rules": [{"citation": "Civil Law Act"}]}}})
        example = _example({"research": {"legal_rules": ["Road Traffic Act 1961 s.65"]}})
        result = legal_element_coverage(run, example)
        assert result["score"] == 0.0

    def test_score_is_one_when_no_expected_rules(self):
        run = _run({"research": {"law": {"legal_rules": []}}})
        example = _example({"research": {"legal_rules": []}})
        assert legal_element_coverage(run, example)["score"] == 1.0

    def test_handles_missing_run_outputs(self):
        example = _example({"research": {"legal_rules": ["RTA s.65"]}})
        result = legal_element_coverage(_run({}), example)
        assert result["score"] == 0.0
