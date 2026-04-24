"""Build a downloadable hearing-prep zip pack for a case (US-020).

Layout produced by ``assemble_pack``::

    manifest.json           # HearingPackManifest (metadata + file list)
    case_summary.md         # Markdown summary (description + status)
    evidence.json           # All evidence rows
    facts.json              # All facts
    arguments.md            # Pretty-printed arguments (one block per side)
    fairness_governance.json # Fairness & governance check report
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
    "fairness_governance.json",
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
    if data.fairness_report:
        lines.extend(
            [
                "## Fairness & Governance Check",
                "",
                f"- **Status:** {data.fairness_report.get('status')}",
                f"- **Summary:** {data.fairness_report.get('summary')}",
                "",
            ]
        )
    if data.decision_history:
        lines.extend(
            [
                "## Decision History",
                "",
                *[
                    (
                        f"- **{entry.get('created_at') or 'unknown'}:** "
                        f"{entry.get('recommended_outcome') or 'No outcome'}"
                    )
                    + (f" ({entry.get('amendment_reason')})" if entry.get("amendment_reason") else "")
                    for entry in data.decision_history
                ],
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
    fairness_governance_payload = {
        "fairness_report": data.fairness_report,
        "decision_history": data.decision_history,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest.model_dump_json(indent=2))
        zf.writestr("case_summary.md", _case_summary_md(data))
        zf.writestr("evidence.json", json.dumps(data.evidence, indent=2, default=str))
        zf.writestr("facts.json", json.dumps(data.facts, indent=2, default=str))
        zf.writestr("arguments.md", _arguments_md(data))
        zf.writestr(
            "fairness_governance.json",
            json.dumps(fairness_governance_payload, indent=2, default=str),
        )
    return buf.getvalue()
