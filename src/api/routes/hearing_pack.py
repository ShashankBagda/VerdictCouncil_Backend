"""Hearing pack generation endpoint."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.api.deps import DBSession, require_role
from src.api.schemas.common import ErrorResponse
from src.api.schemas.hearing_notes import HearingPackResponse
from src.models.case import Case
from src.models.user import User, UserRole

router = APIRouter()


def _listify(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "questions", "suggested_questions"):
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
    return []


@router.post(
    "/{case_id}/hearing-pack",
    response_model=HearingPackResponse,
    operation_id="generate_hearing_pack",
    summary="Generate hearing pack",
    description="Assemble case materials into a consolidated hearing pack for judicial review.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def generate_hearing_pack(
    case_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> HearingPackResponse:
    result = await db.execute(
        select(Case)
        .where(Case.id == case_id)
        .options(
            selectinload(Case.parties),
            selectinload(Case.facts),
            selectinload(Case.evidence),
            selectinload(Case.witnesses),
            selectinload(Case.legal_rules),
            selectinload(Case.precedents),
            selectinload(Case.arguments),
        )
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    parties = [{"id": str(p.id), "name": p.name, "role": p.role.value} for p in case.parties]
    facts = [
        {
            "id": str(f.id),
            "description": f.description,
            "confidence": f.confidence.value if f.confidence else None,
            "status": f.status.value if f.status else None,
        }
        for f in case.facts
    ]
    disputed_issues = [
        {
            "id": str(f.id),
            "description": f.description,
            "confidence": f.confidence.value if f.confidence else None,
            "status": f.status.value if f.status else None,
            "dispute_reason": (f.corroboration or {}).get("dispute_reason")
            if isinstance(f.corroboration, dict)
            else None,
        }
        for f in case.facts
        if f.status and f.status.value == "disputed"
    ]
    evidence = [
        {
            "id": str(e.id),
            "evidence_type": e.evidence_type.value,
            "strength": e.strength.value if e.strength else None,
            "admissibility_flags": e.admissibility_flags,
        }
        for e in case.evidence
    ]
    evidence_gaps = [
        {
            "id": f"weak-{e.id}",
            "type": "weak_evidence",
            "description": f"Weak {e.evidence_type.value} evidence item",
            "status": e.strength.value if e.strength else None,
            "details": e.admissibility_flags or e.linked_claims,
        }
        for e in case.evidence
        if e.strength and e.strength.value == "weak"
    ] + [
        {
            "id": f"uncorroborated-{f.id}",
            "type": "uncorroborated_fact",
            "description": f.description,
            "status": f.status.value if f.status else None,
            "details": f.corroboration,
        }
        for f in case.facts
        if not f.corroboration
    ]
    witnesses = [
        {
            "id": str(w.id),
            "name": w.name,
            "role": w.role,
            "credibility_score": w.credibility_score,
        }
        for w in case.witnesses
    ]
    legal_framework = [
        {
            "id": str(r.id),
            "statute_name": r.statute_name,
            "section": r.section,
            "relevance_score": r.relevance_score,
        }
        for r in case.legal_rules
    ] + [
        {
            "id": str(p.id),
            "citation": p.citation,
            "court": p.court,
            "similarity_score": p.similarity_score,
        }
        for p in case.precedents
    ]

    arguments = {
        "claimant": [
            {
                "id": str(a.id),
                "legal_basis": a.legal_basis,
                "weaknesses": a.weaknesses,
            }
            for a in case.arguments
            if a.side.value == "claimant"
        ],
        "prosecution": [
            {
                "id": str(a.id),
                "legal_basis": a.legal_basis,
                "weaknesses": a.weaknesses,
            }
            for a in case.arguments
            if a.side.value == "prosecution"
        ],
        "respondent": [
            {
                "id": str(a.id),
                "legal_basis": a.legal_basis,
                "weaknesses": a.weaknesses,
            }
            for a in case.arguments
            if a.side.value == "respondent"
        ],
        "defense": [
            {
                "id": str(a.id),
                "legal_basis": a.legal_basis,
                "weaknesses": a.weaknesses,
            }
            for a in case.arguments
            if a.side.value == "defense"
        ],
    }
    suggested_questions = []
    weak_points = []
    for argument in case.arguments:
        if argument.weaknesses:
            weak_points.append(
                {
                    "side": argument.side.value,
                    "issue": argument.legal_basis,
                    "weakness": argument.weaknesses,
                }
            )
        for index, question in enumerate(_listify(argument.suggested_questions)):
            if isinstance(question, dict):
                suggested_questions.append(
                    {
                        "id": question.get("id") or f"{argument.id}-{index}",
                        "side": argument.side.value,
                        "text": question.get("text") or question.get("question"),
                        "type": question.get("type"),
                        "linked_issue": question.get("linked_issue") or argument.legal_basis,
                    }
                )
            elif isinstance(question, str):
                suggested_questions.append(
                    {
                        "id": f"{argument.id}-{index}",
                        "side": argument.side.value,
                        "text": question,
                        "type": None,
                        "linked_issue": argument.legal_basis,
                    }
                )

    current_verdict = case.judicial_decision

    return HearingPackResponse(
        case_id=case.id,
        case_title=case.description or f"Case {case.id}",
        domain=case.domain.value,
        status=case.status.value,
        case_summary=case.description,
        parties=parties,
        facts=facts,
        disputed_issues=disputed_issues,
        evidence=evidence,
        evidence_gaps=evidence_gaps,
        witnesses=witnesses,
        legal_framework=legal_framework,
        arguments=arguments,
        suggested_questions=suggested_questions,
        weak_points=weak_points,
        current_verdict=current_verdict,
        created_at=case.created_at,
        last_updated=case.updated_at or case.created_at,
    )


def _h(level: int, text: str) -> str:
    return f"{'#' * level} {text}\n\n"


def _bullet(text: str) -> str:
    return f"- {text}\n"


def _maybe_section(title: str, body: str) -> str:
    body = body.strip("\n")
    if not body:
        return ""
    return _h(2, title) + body + "\n\n"


def _render_hearing_pack_markdown(case: Case) -> str:
    """Assemble the case dossier into a single markdown document.

    The judge downloads or previews this directly — it must be readable
    standalone with no template chrome from the API client.
    """
    lines: list[str] = []
    lines.append(_h(1, f"Hearing Pack — {case.description or f'Case {case.id}'}"))
    lines.append(f"**Case ID:** `{case.id}`  \n")
    lines.append(f"**Domain:** {case.domain.value}  \n")
    lines.append(f"**Status:** {case.status.value}  \n")
    if case.created_at:
        lines.append(f"**Filed:** {case.created_at.isoformat()}  \n")
    lines.append("\n")

    parties_md = "".join(_bullet(f"**{p.role.value.title()}** — {p.name}") for p in case.parties)
    lines.append(_maybe_section("Parties", parties_md))

    facts_md = "".join(
        _bullet(f"_{f.status.value if f.status else 'unknown'}_ — {f.description}")
        for f in case.facts
    )
    lines.append(_maybe_section("Facts & Timeline", facts_md))

    evidence_md = "".join(
        _bullet(
            f"{e.evidence_type.value.title()}"
            f"{f' (strength: {e.strength.value})' if e.strength else ''}"
        )
        for e in case.evidence
    )
    lines.append(_maybe_section("Evidence", evidence_md))

    witnesses_md = "".join(
        _bullet(
            f"**{w.name}** ({w.role})"
            f"{f' — credibility {w.credibility_score}' if w.credibility_score else ''}"
        )
        for w in case.witnesses
    )
    lines.append(_maybe_section("Witnesses", witnesses_md))

    def _statute_line(r):
        section = f" §{r.section}" if r.section else ""
        if r.relevance_score is not None:
            relevance = f" — relevance {round((r.relevance_score or 0) * 100)}%"
        else:
            relevance = ""
        return _bullet(f"**{r.statute_name}**{section}{relevance}")

    statutes_md = "".join(_statute_line(r) for r in case.legal_rules)
    lines.append(_maybe_section("Applicable Law & Statutes", statutes_md))

    precedents_md = ""
    for p in case.precedents:
        precedents_md += _bullet(
            f"**{p.citation}**"
            f"{f' ({p.court})' if p.court else ''}"
            f"{f' — outcome: {p.outcome}' if p.outcome else ''}"
        )
        if p.reasoning_summary:
            precedents_md += f"  - {p.reasoning_summary}\n"
    lines.append(_maybe_section("Precedents", precedents_md))

    args_md = ""
    for side in ("claimant", "prosecution", "respondent", "defense"):
        side_args = [a for a in case.arguments if a.side.value == side]
        if not side_args:
            continue
        args_md += _h(3, side.title())
        for a in side_args:
            args_md += _bullet(f"**{a.legal_basis}**")
            if a.weaknesses:
                args_md += f"  - _Weaknesses:_ {a.weaknesses}\n"
            for q in _listify(a.suggested_questions):
                if isinstance(q, dict):
                    text = q.get("question") or q.get("text")
                    if text:
                        args_md += f"  - _Q:_ {text}\n"
                elif isinstance(q, str):
                    args_md += f"  - _Q:_ {q}\n"
    lines.append(_maybe_section("Arguments & Suggested Questions", args_md))

    if case.judicial_decision:
        lines.append(_maybe_section("Judicial Decision", str(case.judicial_decision)))

    lines.append(
        "---\n\n"
        "_AI-assisted judicial preparation material; all findings subject to "
        "judicial determination; no finding constitutes a verdict._\n"
    )
    return "".join(lines)


@router.get(
    "/{case_id}/hearing-pack.md",
    operation_id="get_hearing_pack_markdown",
    summary="Get hearing pack as a downloadable markdown file",
    description=(
        "Assembles the case dossier into a single markdown document. "
        "Returned as text/markdown with a Content-Disposition attachment "
        "header so browsers offer it as a file download. The frontend also "
        "renders the same content inline via react-markdown."
    ),
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def get_hearing_pack_markdown(
    case_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> Response:
    result = await db.execute(
        select(Case)
        .where(Case.id == case_id)
        .options(
            selectinload(Case.parties),
            selectinload(Case.facts),
            selectinload(Case.evidence),
            selectinload(Case.witnesses),
            selectinload(Case.legal_rules),
            selectinload(Case.precedents),
            selectinload(Case.arguments),
        )
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    markdown = _render_hearing_pack_markdown(case)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="hearing-pack-{case_id}.md"'},
    )
