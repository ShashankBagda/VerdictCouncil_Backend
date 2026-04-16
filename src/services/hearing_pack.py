"""Build a downloadable hearing-prep zip pack for a case (US-020).

Layout produced by ``assemble_pack``::

    manifest.json      # HearingPackManifest (metadata + file list)
    case_summary.md    # Markdown summary (description + status + verdict)
    evidence.json      # All evidence rows
    facts.json         # All facts
    arguments.md       # Pretty-printed arguments (one block per side)
    verdict.json       # Verdict + fairness_report
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime

from src.api.schemas.hearing_pack import HearingPackManifest
from src.services.case_report_data import CaseReportData

_FILES = [
    "manifest.json",
    "case_summary.md",
    "evidence.json",
    "facts.json",
    "arguments.md",
    "verdict.json",
]


def _case_summary_md(data: CaseReportData) -> str:
    lines = [
        f"# Case {data.case_id}",
        "",
        f"- **Domain:** {data.domain}",
        f"- **Status:** {data.status}",
        f"- **Created at:** {data.created_at.isoformat()}",
        "",
        "## Description",
        "",
        data.description or "_No description provided._",
        "",
    ]
    if data.verdict:
        lines.extend(
            [
                "## Verdict",
                "",
                f"- **Recommendation:** {data.verdict.get('recommendation_type')}",
                f"- **Outcome:** {data.verdict.get('recommended_outcome')}",
                f"- **Confidence:** {data.verdict.get('confidence_score')}",
                "",
            ]
        )
    return "\n".join(lines)


def _arguments_md(data: CaseReportData) -> str:
    if not data.arguments:
        return "# Arguments\n\n_No arguments recorded._\n"
    lines = ["# Arguments", ""]
    for arg in data.arguments:
        lines.append(f"## {arg.get('side', 'unknown').title()}")
        lines.append("")
        lines.append(f"**Legal basis:** {arg.get('legal_basis', '')}")
        lines.append("")
        if arg.get("weaknesses"):
            lines.append(f"**Weaknesses:** {arg['weaknesses']}")
            lines.append("")
    return "\n".join(lines)


def _build_manifest(data: CaseReportData) -> HearingPackManifest:
    return HearingPackManifest(
        case_id=data.case_id,
        domain=data.domain,
        status=data.status,
        generated_at=datetime.now(UTC),
        files=_FILES,
        counts={
            "parties": len(data.parties),
            "evidence": len(data.evidence),
            "facts": len(data.facts),
            "arguments": len(data.arguments),
        },
    )


def assemble_pack(data: CaseReportData) -> bytes:
    """Produce the in-memory zip bytes for a case's hearing pack."""
    manifest = _build_manifest(data)
    verdict_payload = {
        "verdict": data.verdict,
        "fairness_report": data.fairness_report,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest.model_dump_json(indent=2))
        zf.writestr("case_summary.md", _case_summary_md(data))
        zf.writestr("evidence.json", json.dumps(data.evidence, indent=2, default=str))
        zf.writestr("facts.json", json.dumps(data.facts, indent=2, default=str))
        zf.writestr("arguments.md", _arguments_md(data))
        zf.writestr("verdict.json", json.dumps(verdict_payload, indent=2, default=str))
    return buf.getvalue()
