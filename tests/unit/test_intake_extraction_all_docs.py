"""Unit tests for the intake extractor's expanded document set.

Locks the contract that `_build_user_prompt` emits blocks for EVERY
uploaded document kind — not just the authoritative lead pleading.
The upstream filter in `run_intake_extraction` (covered by code review
of the diff) gates whether to run at all on the presence of a lead
pleading, but on success feeds every uploaded document into the LLM.
The previous behaviour silently dropped supporting documents
(witness statements, evidence bundles), so any claim grounded in those
documents had no citation back to its source.

Why this matters: the intake agent prompt names `intake_extraction` as
the authoritative source for parties / offence / claim particulars;
when supporting docs are missing from the extraction's citation set,
the agent's "ground truth" is silently incomplete.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from src.models.case import DocumentKind
from src.services.intake_extraction import _build_user_prompt


def _doc(kind: DocumentKind, filename: str) -> SimpleNamespace:
    """Minimal Document stand-in — only the fields _build_user_prompt reads."""
    return SimpleNamespace(id=uuid.uuid4(), kind=kind, filename=filename)


class TestBuildUserPromptDocSet:
    def test_lead_and_supporting_both_emit_blocks(self):
        """Notice + witness statement + evidence bundle: every text-bearing
        document must produce its own header + body block."""
        docs = [
            (_doc(DocumentKind.notice_of_traffic_offence, "notice.pdf"),
             "NOTICE OF TRAFFIC OFFENCE\nVehicle SGA77F at 70 km/h…"),
            (_doc(DocumentKind.witness_statement, "officer.pdf"),
             "I, Officer Rahman Bin Yusof, recorded the vehicle…"),
            (_doc(DocumentKind.evidence_bundle, "calibration.pdf"),
             "Calibration certificate dated 2021-01-15…"),
        ]
        prompt = _build_user_prompt(docs, correction=None)

        assert "Traffic Notice (Summons or Advisory)" in prompt
        assert "Witness Statement / Affidavit" in prompt
        assert "Other Supporting Documents" in prompt
        assert "notice.pdf" in prompt
        assert "officer.pdf" in prompt
        assert "calibration.pdf" in prompt
        assert "Vehicle SGA77F" in prompt
        assert "Officer Rahman" in prompt
        assert "Calibration certificate" in prompt

    def test_empty_text_documents_are_skipped(self):
        """Cache-miss documents (parsed_text empty) are silently skipped —
        the LLM gets only docs with extractable text. Caller's job to
        log the gap; not the prompt builder's."""
        docs = [
            (_doc(DocumentKind.notice_of_traffic_offence, "notice.pdf"),
             "real text here"),
            (_doc(DocumentKind.witness_statement, "scan.pdf"), ""),
            (_doc(DocumentKind.witness_statement, "blanks.pdf"), "   \n  "),
        ]
        prompt = _build_user_prompt(docs, correction=None)

        assert "real text here" in prompt
        assert "scan.pdf" not in prompt
        assert "blanks.pdf" not in prompt

    def test_correction_appended_after_documents(self):
        """A judge correction lands as its own block after the documents,
        marked authoritative."""
        docs = [
            (_doc(DocumentKind.notice_of_traffic_offence, "n.pdf"),
             "doc text"),
        ]
        prompt = _build_user_prompt(docs, correction="The accused is BEN LIM, not BEN LIN.")

        assert "doc text" in prompt
        assert "Judge's correction" in prompt
        assert "BEN LIM, not BEN LIN" in prompt
        # The correction block must come AFTER the document blocks so
        # the LLM sees the source first and the correction as override.
        assert prompt.index("doc text") < prompt.index("Judge's correction")

    def test_no_documents_yields_placeholder(self):
        """If every document has empty text (all cache-miss), the prompt
        still constructs cleanly with a placeholder body."""
        docs = [
            (_doc(DocumentKind.notice_of_traffic_offence, "n.pdf"), ""),
        ]
        prompt = _build_user_prompt(docs, correction=None)

        assert "(no parseable document text available)" in prompt

    def test_kind_label_covers_every_supporting_kind(self):
        """Every DocumentKind that can be uploaded must have a label —
        the slot collapse and the future expansion of slot kinds both
        rely on this. A KeyError here means a new DocumentKind value
        was added without updating _kind_label."""
        # Pick the kinds the slot-collapse may emit. evidence_bundle is
        # the catch-all; notice/charge are the lead pleadings.
        for kind in (
            DocumentKind.notice_of_traffic_offence,
            DocumentKind.charge_sheet,
            DocumentKind.witness_statement,
            DocumentKind.police_report,
            DocumentKind.speed_camera_record,
            DocumentKind.medical_report,
            DocumentKind.letter_of_mitigation,
            DocumentKind.evidence_bundle,
            DocumentKind.in_car_camera,
            DocumentKind.other,
        ):
            docs = [(_doc(kind, "f.pdf"), "x")]
            prompt = _build_user_prompt(docs, correction=None)
            assert "f.pdf" in prompt
