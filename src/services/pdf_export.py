"""PDF rendering for case reports (US-027).

Uses Jinja2 to render an HTML template, then WeasyPrint to convert
that HTML to PDF bytes. Reuses the ``CaseReportData`` projection
from the hearing-pack export so the two surfaces cannot drift.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from src.services.case_report_data import CaseReportData

_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
)


def render_case_report_pdf(data: CaseReportData) -> bytes:
    """Render the case report HTML template to PDF bytes."""
    template = _env.get_template("case_report.html")
    html_str = template.render(case=data)
    return HTML(string=html_str).write_pdf()
