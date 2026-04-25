"""Sprint 3 3.B.4 — Pydantic output schemas carry citation provenance.

Both `LegalRule` and `Precedent` must accept a `supporting_sources` list
of `source_id` strings (populated by 3.B.5 from the run's tool-artifact
chain). The field is optional with an empty-list default so legacy outputs
still parse during the rollout.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.pipeline.graph.schemas import LegalRule, Precedent


class TestLegalRuleSupportingSources:
    def test_field_present_with_default_empty_list(self):
        rule = LegalRule(
            rule_id="r-1",
            jurisdiction="SG",
            citation="Act s.1",
            text="…",
            applicability="…",
        )
        assert rule.supporting_sources == []

    def test_accepts_source_id_list(self):
        rule = LegalRule(
            rule_id="r-1",
            jurisdiction="SG",
            citation="Act s.1",
            text="…",
            applicability="…",
            supporting_sources=["file-1:abcdef012345", "file-2:fedcba543210"],
        )
        assert rule.supporting_sources == ["file-1:abcdef012345", "file-2:fedcba543210"]

    def test_legacy_payload_without_field_parses(self):
        rule = LegalRule.model_validate(
            {
                "rule_id": "r-1",
                "jurisdiction": "SG",
                "citation": "Act s.1",
                "text": "…",
                "applicability": "…",
            }
        )
        assert rule.supporting_sources == []

    def test_extra_fields_still_forbidden(self):
        with pytest.raises(ValidationError):
            LegalRule(
                rule_id="r-1",
                jurisdiction="SG",
                citation="Act s.1",
                text="…",
                applicability="…",
                bogus="nope",  # type: ignore[call-arg]
            )


class TestPrecedentSupportingSources:
    def test_field_present_with_default_empty_list(self):
        prec = Precedent(
            case_name="Tan v Tan",
            citation="[2020] SGHC 1",
            jurisdiction="SG",
            holding="…",
            relevance_rationale="…",
        )
        assert prec.supporting_sources == []

    def test_accepts_source_id_list(self):
        prec = Precedent(
            case_name="Tan v Tan",
            citation="[2020] SGHC 1",
            jurisdiction="SG",
            holding="…",
            relevance_rationale="…",
            supporting_sources=["file-9:abc123def456"],
        )
        assert prec.supporting_sources == ["file-9:abc123def456"]

    def test_legacy_payload_without_field_parses(self):
        prec = Precedent.model_validate(
            {
                "case_name": "Tan v Tan",
                "citation": "[2020] SGHC 1",
                "jurisdiction": "SG",
                "holding": "…",
                "relevance_rationale": "…",
            }
        )
        assert prec.supporting_sources == []

    def test_extra_fields_still_forbidden(self):
        with pytest.raises(ValidationError):
            Precedent(
                case_name="Tan v Tan",
                citation="[2020] SGHC 1",
                jurisdiction="SG",
                holding="…",
                relevance_rationale="…",
                bogus="nope",  # type: ignore[call-arg]
            )
