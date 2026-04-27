"""Pre-pipeline intake extraction.

Given a draft case with typed documents uploaded by the judge, ask the
OpenAI Responses API to propose the structured fields a case needs before
the 9-agent pipeline can run: parties (Prosecution / Accused), offence
code, title, description, filed date, and (for small claims) claim amount.

The extractor is the "docs-as-source-of-truth" half of the intake flow —
the judge confirms, corrects or overrides via the chat surface in the
frontend; nothing here is authoritative until the confirm endpoint writes
those same fields back onto the Case row.

Design notes:
  - Structured output (JSON Schema, strict mode) so we never have to parse
    prose. The shape mirrors the confirm payload so the frontend can drop
    it into the form verbatim on "I'll type it" fallback.
  - Confidence is model-self-reported (low|medium|high per field). The
    Responses API does not expose per-field logprobs, so we treat this as
    a UX hint only — never a gate.
  - Citations point back to Document.id + page + verbatim quote so the
    judge can click through to the source text in the chat surface.
  - Idempotent at the worker boundary: the caller is a pipeline_jobs row,
    so redelivery from the dispatcher rewrites the same row and re-emits
    the same terminal event.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import openai
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.case import Case, CaseStatus, Document, DocumentKind
from src.services.intake_events import publish_intake_event
from src.shared.config import settings
from src.tools.parse_document import parse_document

logger = logging.getLogger(__name__)

# Documents we trust for facts-of-the-case. An evidence bundle or in-car
# camera clip might supply corroboration later in the pipeline but shouldn't
# be the primary source for party/offence metadata at intake.
_AUTHORITATIVE_KINDS = {
    DocumentKind.notice_of_traffic_offence,
    DocumentKind.charge_sheet,
    DocumentKind.other,  # pre-existing uploads before the typed-slot rollout
}

_EXTRACTION_SYSTEM_PROMPT = """\
You extract structured intake facts from typed documents uploaded for a
Singapore court case. You are grounded in the document text only — do not
invent facts. If a field is not present in the documents, leave it null and
say so in `notes`.

Extract:
  - title: a short case label, e.g. "PP v K. Lim (Reckless driving)"
  - description: 1-2 sentence factual summary of the alleged offence/claim
  - filed_date: ISO date (YYYY-MM-DD) when the matter was filed/charged
  - parties: every named party with role one of
      prosecution, accused, claimant, respondent
  - offence_code: the section cited, verbatim (e.g. "S65AA", "S67(1)(b)").
    Do NOT normalise, rewrite or validate against a list — the sitting
    judge is the authority. Traffic matters always have one; small-claims
    matters have none.
  - claim_amount: numeric SGD amount for small-claims matters, else null
  - is_advisory_only: true when the notice explicitly says no summons
    action will be taken (e.g. phrases like "no summons action will be
    taken", "this letter serves as an advisory", "advisory notice for
    traffic offence"); false when a summons or charge sheet is active.
    Null when no notice/charge sheet is present to judge from.

For every non-null field, add a citation: document_id (UUID), page (1-based
int or null), and a short verbatim quote. Report self-rated confidence
(low|medium|high) per field.

The user prompt may include the lead pleading (Notice of Traffic Offence
or Charge Sheet) PLUS supporting documents (witness statements, police
reports, evidence bundles). Treat the lead pleading as primary for
offence_code, claim_amount, and is_advisory_only. Use supporting
documents to corroborate parties, dates, and the description — and
prefer citing the supporting document when it carries a more specific
quote (e.g. an officer's witness statement naming the recorded speed,
a calibration certificate dating the equipment). When supporting and
lead disagree, raise the confidence on the lead-pleading reading and
flag the disagreement in `notes`.
"""

# JSON Schema for Responses API strict structured output. Keep in sync with
# the Pydantic parse below — strict mode rejects unknown properties.
_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fields", "confidences", "citations", "notes"],
    "properties": {
        "fields": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "title",
                "description",
                "filed_date",
                "parties",
                "offence_code",
                "claim_amount",
                "is_advisory_only",
            ],
            "properties": {
                "title": {"type": ["string", "null"]},
                "description": {"type": ["string", "null"]},
                "filed_date": {"type": ["string", "null"]},
                "parties": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "role"],
                        "properties": {
                            "name": {"type": "string"},
                            "role": {
                                "type": "string",
                                "enum": [
                                    "prosecution",
                                    "accused",
                                    "claimant",
                                    "respondent",
                                ],
                            },
                        },
                    },
                },
                "offence_code": {"type": ["string", "null"]},
                "claim_amount": {"type": ["number", "null"]},
                "is_advisory_only": {"type": ["boolean", "null"]},
            },
        },
        "confidences": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "title",
                "description",
                "filed_date",
                "parties",
                "offence_code",
                "claim_amount",
                "is_advisory_only",
            ],
            "properties": {
                k: {"type": "string", "enum": ["low", "medium", "high"]}
                for k in (
                    "title",
                    "description",
                    "filed_date",
                    "parties",
                    "offence_code",
                    "claim_amount",
                    "is_advisory_only",
                )
            },
        },
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["field", "document_id", "page", "quote"],
                "properties": {
                    "field": {"type": "string"},
                    "document_id": {"type": "string"},
                    "page": {"type": ["integer", "null"]},
                    "quote": {"type": "string"},
                },
            },
        },
        "notes": {"type": ["string", "null"]},
    },
}


async def _document_text(db: AsyncSession, doc: Document) -> str:
    """Return the plain-text content of a document, reading the cached
    pages JSONB when available and falling back to a live parse_document
    call otherwise. Unparseable docs return an empty string so the caller
    can still attempt extraction from the other uploads."""
    if doc.pages:
        texts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in doc.pages]
        joined = "\n\n".join(t for t in texts if t)
        if joined.strip():
            return joined
    if not doc.openai_file_id:
        return ""
    try:
        parsed = await parse_document(doc.openai_file_id, extract_tables=False)
    except Exception as exc:
        logger.warning("intake parse_document failed for %s: %s", doc.id, exc)
        return ""
    # Side-effect: persist the pages so subsequent runs (or the pipeline)
    # don't pay the parse cost again.
    doc.pages = parsed.get("pages") or []
    await db.flush()
    return parsed.get("text", "") or ""


def _kind_label(kind: DocumentKind) -> str:
    return {
        DocumentKind.notice_of_traffic_offence: "Traffic Notice (Summons or Advisory)",
        DocumentKind.charge_sheet: "Charge Sheet",
        DocumentKind.police_report: "Police Report",
        DocumentKind.witness_statement: "Witness Statement / Affidavit",
        DocumentKind.speed_camera_record: "Speed Camera Record",
        DocumentKind.evidence_bundle: "Other Supporting Documents",
        DocumentKind.in_car_camera: "In-car camera footage (descriptive only)",
        DocumentKind.medical_report: "Medical Report",
        DocumentKind.letter_of_mitigation: "Letter of Mitigation",
        DocumentKind.other: "Untyped document",
    }[kind]


def _build_user_prompt(
    documents: list[tuple[Document, str]],
    correction: str | None,
) -> str:
    blocks: list[str] = []
    for doc, text in documents:
        if not text.strip():
            continue
        header = f"=== [{_kind_label(doc.kind)}]  id={doc.id}  filename={doc.filename} ==="
        blocks.append(f"{header}\n{text.strip()}")
    joined = "\n\n".join(blocks) if blocks else "(no parseable document text available)"
    if correction:
        joined += (
            "\n\n=== Judge's correction ===\n"
            f"{correction.strip()}\n"
            "Re-extract taking this correction as authoritative over the documents."
        )
    return joined


async def run_intake_extraction(
    db: AsyncSession,
    *,
    case_id: UUID,
    correction: str | None = None,
) -> dict[str, Any]:
    """Load case documents, call the Responses API, persist the proposed
    fields on Case.intake_extraction, and fan out intake events. Returns
    the same payload that was persisted.

    Callers are either the arq worker (first-pass on draft upload) or the
    /intake/message endpoint (subsequent judge corrections). Both paths
    share the same extraction logic and the same terminal `done` event.
    """
    case = await db.get(Case, case_id)
    if case is None:
        raise ValueError(f"Case {case_id} not found for intake extraction")

    await publish_intake_event(
        case_id,
        {"type": "status", "phase": "loading_documents", "ts": _now()},
    )

    docs_result = await db.execute(select(Document).where(Document.case_id == case_id))
    all_docs = list(docs_result.scalars())
    authoritative = [d for d in all_docs if d.kind in _AUTHORITATIVE_KINDS]

    if not authoritative:
        payload = _empty_extraction(
            "No authoritative documents uploaded yet. Upload a Notice of "
            "Traffic Offence / Summons or Charge Sheet, or use the 'I'll "
            "type it' fallback to enter the fields manually."
        )
        case.intake_extraction = payload
        case.status = CaseStatus.awaiting_intake_confirmation
        await db.commit()
        await publish_intake_event(case_id, {"type": "done", "extraction": payload, "ts": _now()})
        return payload

    await publish_intake_event(
        case_id, {"type": "status", "phase": "parsing_documents", "ts": _now()}
    )
    # Feed every uploaded document into the extractor — not only the
    # authoritative ones. The lead pleading (notice / charge sheet) gates
    # whether we run at all (above) and is the primary source for
    # offence_code / claim_amount, but supporting documents (witness
    # statements, evidence bundles) corroborate parties / dates / facts
    # and must be citable from `intake_extraction.citations`. Earlier
    # behaviour silently dropped the bundle so the intake agent's
    # downstream "treat intake_extraction as ground truth" reading
    # missed half the evidence. Lead pleadings are listed first so the
    # model attends to them as the primary frame.
    supporting = [d for d in all_docs if d not in authoritative]
    ordered_docs = [*authoritative, *supporting]
    docs_with_text = [(d, await _document_text(db, d)) for d in ordered_docs]

    await publish_intake_event(
        case_id, {"type": "status", "phase": "extracting_fields", "ts": _now()}
    )

    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    # The efficient-reasoning model (gpt-5-mini) requires org verification on
    # OpenAI's side; default to the lightweight tier that the rest of the
    # app (parse_document, guardrails, vector_store_fallback, ...) already
    # uses. Override via OPENAI_MODEL_INTAKE once the org is verified.
    extractor_model = settings.openai_model_intake or settings.openai_model_lightweight
    try:
        response = await client.responses.create(
            model=extractor_model,
            input=[
                {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(docs_with_text, correction)},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "intake_extraction",
                    "schema": _EXTRACTION_SCHEMA,
                    "strict": True,
                }
            },
        )
        raw = response.output_text
        parsed = json.loads(raw)
    except Exception as exc:
        logger.exception("intake extraction LLM call failed for case %s", case_id)
        # Roll status back to draft so the frontend re-enables upload and the
        # judge can retry or fall through to "I'll type it".
        case.status = CaseStatus.draft
        await db.commit()
        await publish_intake_event(
            case_id,
            {"type": "error", "message": f"{type(exc).__name__}: {exc}", "ts": _now()},
        )
        raise

    payload = {
        **parsed,
        "model": response.model if hasattr(response, "model") else None,
        "ran_at": _now(),
    }
    case.intake_extraction = payload
    case.status = CaseStatus.awaiting_intake_confirmation
    await db.commit()

    await publish_intake_event(case_id, {"type": "done", "extraction": payload, "ts": _now()})
    return payload


def _empty_extraction(note: str) -> dict[str, Any]:
    return {
        "fields": {
            "title": None,
            "description": None,
            "filed_date": None,
            "parties": [],
            "offence_code": None,
            "claim_amount": None,
            "is_advisory_only": None,
        },
        "confidences": {
            "title": "low",
            "description": "low",
            "filed_date": "low",
            "parties": "low",
            "offence_code": "low",
            "claim_amount": "low",
            "is_advisory_only": "low",
        },
        "citations": [],
        "notes": note,
        "model": None,
        "ran_at": _now(),
    }


def _now() -> str:
    return datetime.now(UTC).isoformat()
