"""LangChain @tool wrappers for VerdictCouncil domain tools.

`make_tools(state, agent_name)` returns the subset of LangChain tools that
the given agent is allowed to call, with vector_store_id pre-injected via
closure over the case state. It also returns a `PrecedentMetaSideChannel`
that accumulates search_precedents metadata across multiple tool calls in a
node; the caller folds this into CaseState.precedent_source_metadata at exit.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from langchain_core.documents import Document
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.pipeline.graph.prompts import AGENT_TOOLS
from src.pipeline.graph.state import GraphState


def _merge_precedent_meta(
    existing: dict[str, Any] | None,
    new: dict[str, Any],
) -> dict[str, Any]:
    """Worst-of merge across multiple search_precedents calls in one node turn.

    First call wins for the initial snapshot. Any subsequent call with
    source_failed=True escalates the merged record and adopts that call's
    pair_status. Other fields flow through from the existing record.
    """
    if existing is None:
        return dict(new)
    if new.get("source_failed"):
        existing["source_failed"] = True
        existing["pair_status"] = new.get("pair_status", existing.get("pair_status"))
    return existing


# ---------------------------------------------------------------------------
# Precedent metadata side-channel
# ---------------------------------------------------------------------------


class PrecedentMetaSideChannel:
    """Accumulates worst-of precedent metadata across calls in one node turn."""

    def __init__(self) -> None:
        self._meta: dict[str, Any] | None = None

    def record(self, metadata: dict[str, Any]) -> None:
        self._meta = _merge_precedent_meta(self._meta, metadata)

    @property
    def metadata(self) -> dict[str, Any] | None:
        return self._meta


# ---------------------------------------------------------------------------
# Citation provenance helpers (Sprint 3 Workstream B)
# ---------------------------------------------------------------------------


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _resolve_file_id(item: dict[str, Any], surrogate_prefix: str) -> str:
    """Return a stable identifier for an item's source document.

    OpenAI vector-store hits expose a real ``file_id``; live PAIR results
    and any other source without one synthesise a surrogate from the URL
    (or citation as last resort) so every artifact carries a verifiable
    provenance key.

    The surrogate uses a hyphen (``pair-<hash>``) rather than a colon
    so the final ``source_id`` keeps the documented one-colon shape:
    ``<file_id>:<content_hash>``. With ``pair:<hash>`` the surrogate
    forced a three-token id and broke the ``source_id.split(":", 1)``
    contract that downstream consumers (golden-case validation, audit
    parsing) rely on.
    """
    file_id = item.get("file_id")
    if file_id:
        return str(file_id)
    surrogate = item.get("url") or item.get("citation") or ""
    digest = hashlib.sha256(surrogate.encode("utf-8")).hexdigest()[:12]
    return f"{surrogate_prefix}-{digest}"


def _precedent_to_document(precedent: dict[str, Any]) -> Document:
    """Project a precedent dict into a Document carrying citation provenance.

    The page_content is a deterministic projection of the citable fields so
    the resulting `source_id` is reproducible across runs.
    """
    citation = precedent.get("citation", "")
    summary = precedent.get("reasoning_summary", "")
    page_content = f"{citation}\n{summary}".strip()
    file_id = _resolve_file_id(precedent, surrogate_prefix="pair")
    source_id = f"{file_id}:{_content_hash(page_content)}"
    # Upstream search backends emit different keys: the precedent fetcher
    # historically returns ``similarity_score``; the domain-guidance
    # fetcher returns ``score``. Read both so the projector tolerates
    # either source shape, and always normalise to ``score`` in the
    # Document metadata so downstream consumers see one key.
    score = precedent.get("similarity_score", precedent.get("score", 0))
    return Document(
        page_content=page_content,
        metadata={
            "source_id": source_id,
            "file_id": file_id,
            "filename": citation,
            "score": score,
            "url": precedent.get("url", ""),
            "source": precedent.get("source", ""),
        },
    )


def _guidance_to_document(guidance: dict[str, Any]) -> Document:
    """Project a domain-guidance dict into a Document with citation provenance.

    Domain guidance always hits an OpenAI vector store, so the `file_id`
    is normally present; we still call `_resolve_file_id` defensively in
    case the impl returns a result without one.
    """
    citation = guidance.get("citation", "")
    content = guidance.get("content", "")
    page_content = f"{citation}\n{content}".strip()
    file_id = _resolve_file_id(guidance, surrogate_prefix="guidance")
    source_id = f"{file_id}:{_content_hash(page_content)}"
    # See _precedent_to_document — accept either `score` or
    # `similarity_score` from upstream and normalise to `score` here.
    score = guidance.get("score", guidance.get("similarity_score", 0))
    return Document(
        page_content=page_content,
        metadata={
            "source_id": source_id,
            "file_id": file_id,
            "filename": citation,
            "score": score,
            "source": guidance.get("source", "domain_guidance"),
        },
    )


def _format_results_for_llm(results: list[dict[str, Any]]) -> str:
    """Render search results back to the agent as JSON.

    Keeps prompts backward-compatible while artifacts carry typed Documents
    to the audit layer.
    """
    return json.dumps(results)


# ---------------------------------------------------------------------------
# Tool schemas (Pydantic input models)
# ---------------------------------------------------------------------------


class _ParseDocumentInput(BaseModel):
    file_id: str = Field(description="OpenAI File ID of the uploaded document")
    extract_tables: bool = Field(True, description="Whether to extract tabular data")
    ocr_enabled: bool = Field(False, description="Enable OCR for scanned/image documents")


class _CrossReferenceInput(BaseModel):
    segments: list[dict[str, Any]] = Field(
        description="Document segments to compare. Each: {doc_id, text, page, paragraph}"
    )
    check_type: str = Field(description="Type of check: 'contradiction' | 'corroboration' | 'all'")


class _TimelineConstructInput(BaseModel):
    events: list[dict[str, Any]] = Field(
        description="Events to order. Each: {fact_id, date, event, source_refs}"
    )


class _GenerateQuestionsInput(BaseModel):
    argument_summary: str = Field(description="Summary of the argument or testimony")
    weaknesses: list[str] = Field(description="Identified weaknesses or gaps to probe")
    question_types: list[str] | None = Field(
        None,
        description=(
            "Types of questions: 'factual_clarification' | 'evidence_gap'"
            " | 'credibility_probe' | 'legal_interpretation'"
        ),
    )
    max_questions: int = Field(5, description="Maximum number of questions to generate")


class _ConfidenceCalcInput(BaseModel):
    evidence_strengths: list[str] = Field(
        description="Strength labels per evidence item: 'strong' | 'moderate' | 'weak' | 'insufficient'"  # noqa: E501
    )
    fact_statuses: list[str] = Field(
        description="Status labels per extracted fact: 'established' | 'disputed' | 'unverified'"
    )
    witness_scores: list[int] = Field(description="Credibility scores per witness (0-100)")
    precedent_similarities: list[float] = Field(
        description="Similarity scores per precedent (0.0-1.0)"
    )


class _SearchPrecedentsInput(BaseModel):
    query: str = Field(description="Targeted query for legal concepts or statutory provisions")
    domain: str = Field("small_claims", description="Legal domain: 'small_claims' | 'traffic'")
    max_results: int = Field(5, description="Maximum number of precedents to return")


class _SearchDomainGuidanceInput(BaseModel):
    query: str = Field(
        description="Semantic query for statutes, practice directions, or bench books"
    )
    max_results: int = Field(5, description="Maximum number of guidance results to return")


class _AskJudgeInput(BaseModel):
    question: str = Field(
        description=(
            "A single-sentence question for the presiding Judge. Use this for "
            "calibration calls only the Judge can make — framing weight, "
            "interpretive priorities, escalation thresholds — NOT for "
            "confirmation, restatement, or facts already on raw_documents / "
            "intake_extraction / upstream phase outputs."
        )
    )


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def make_tools(
    state: GraphState,
    agent_name: str,
) -> tuple[list[Any], PrecedentMetaSideChannel]:
    """Build the LangChain tool list for an agent node.

    Returns (tools, precedent_meta_channel). The caller passes `tools` to
    ChatOpenAI.bind_tools() and reads `precedent_meta_channel.metadata` at
    node exit to fold into CaseState.precedent_source_metadata.

    Vector store injection: both search tools receive domain_vector_store_id
    from the case state via closure — the LLM never needs to pass this arg.
    """
    vector_store_id: str | None = state["case"].domain_vector_store_id
    precedent_meta = PrecedentMetaSideChannel()

    allowed_names = set(AGENT_TOOLS.get(agent_name, []))
    all_tools: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # parse_document
    # ------------------------------------------------------------------
    @tool("parse_document", args_schema=_ParseDocumentInput)
    async def parse_document_tool(
        file_id: str,
        extract_tables: bool = True,
        ocr_enabled: bool = False,
    ) -> dict:
        """Parse an uploaded document via the OpenAI Files API.

        Use this to extract text, tables, and metadata from case documents.
        The file_id must be a valid OpenAI File ID already uploaded to the API.
        """
        from src.tools.parse_document import parse_document

        return await parse_document(
            file_id=file_id,
            extract_tables=extract_tables,
            ocr_enabled=ocr_enabled,
        )

    all_tools["parse_document"] = parse_document_tool

    # ------------------------------------------------------------------
    # cross_reference
    # ------------------------------------------------------------------
    @tool("cross_reference", args_schema=_CrossReferenceInput)
    async def cross_reference_tool(
        segments: list[dict[str, Any]],
        check_type: str,
    ) -> dict:
        """Compare document segments to find contradictions and corroborations.

        Use this to detect factual conflicts or agreements across testimony,
        exhibits, and witness statements. check_type: 'contradiction' |
        'corroboration' | 'all'.
        """
        from src.tools.cross_reference import cross_reference

        return await cross_reference(segments=segments, check_type=check_type)

    all_tools["cross_reference"] = cross_reference_tool

    # ------------------------------------------------------------------
    # timeline_construct
    # ------------------------------------------------------------------
    @tool("timeline_construct", args_schema=_TimelineConstructInput)
    async def timeline_construct_tool(events: list[dict[str, Any]]) -> list[dict]:
        """Build a chronological timeline from extracted events.

        Takes events with date/time information, sorts them chronologically,
        and returns an ordered timeline. Events without parseable dates are
        placed at the end.
        """
        from src.tools.timeline_construct import timeline_construct

        return await asyncio.to_thread(timeline_construct, events=events)  # type: ignore[arg-type]

    all_tools["timeline_construct"] = timeline_construct_tool

    # ------------------------------------------------------------------
    # generate_questions
    # ------------------------------------------------------------------
    @tool("generate_questions", args_schema=_GenerateQuestionsInput)
    async def generate_questions_tool(
        argument_summary: str,
        weaknesses: list[str],
        question_types: list[str] | None = None,
        max_questions: int = 5,
    ) -> list[dict]:
        """Generate suggested judicial questions based on argument analysis.

        Use this to probe weaknesses in testimony or argument. Supply the
        summary and a list of identified weaknesses to get targeted questions.
        """
        from src.tools.generate_questions import generate_questions

        return await generate_questions(
            argument_summary=argument_summary,
            weaknesses=weaknesses,
            question_types=question_types,
            max_questions=max_questions,
        )

    all_tools["generate_questions"] = generate_questions_tool

    # ------------------------------------------------------------------
    # confidence_calc
    # ------------------------------------------------------------------
    @tool("confidence_calc", args_schema=_ConfidenceCalcInput)
    def confidence_calc_tool(
        evidence_strengths: list[str],
        fact_statuses: list[str],
        witness_scores: list[int],
        precedent_similarities: list[float],
    ) -> dict:
        """Calculate verdict confidence score from component inputs.

        Combines evidence strength, fact status, witness credibility, and
        precedent similarity into a weighted confidence score (0-100).
        Use this after all Gate-2 analysis is complete.
        """
        from src.tools.confidence_calc import confidence_calc

        return confidence_calc(
            evidence_strengths=evidence_strengths,
            fact_statuses=fact_statuses,
            witness_scores=witness_scores,
            precedent_similarities=precedent_similarities,
        )

    all_tools["confidence_calc"] = confidence_calc_tool

    # ------------------------------------------------------------------
    # search_precedents  (vector_store_id injected via closure)
    # ------------------------------------------------------------------
    @tool(
        "search_precedents",
        args_schema=_SearchPrecedentsInput,
        response_format="content_and_artifact",
    )
    async def search_precedents_tool(
        query: str,
        domain: str = "small_claims",
        max_results: int = 5,
    ) -> tuple[str, list[Document]]:
        """Query the PAIR Search API for binding higher court case law.

        Use this to find precedent cases matching the current fact pattern.
        domain must be 'small_claims' or 'traffic'. Do not pass vector_store_id
        — it is injected automatically from the case context.
        """
        from src.tools.search_precedents import search_precedents_with_meta

        result = await search_precedents_with_meta(
            query=query,
            domain=domain,
            max_results=max_results,
            vector_store_id=vector_store_id,
        )
        precedent_meta.record(result.metadata)
        artifact = [_precedent_to_document(p) for p in result.precedents]
        return _format_results_for_llm(result.precedents), artifact

    all_tools["search_precedents"] = search_precedents_tool

    # ------------------------------------------------------------------
    # search_domain_guidance  (vector_store_id injected via closure)
    # ------------------------------------------------------------------
    @tool(
        "search_domain_guidance",
        args_schema=_SearchDomainGuidanceInput,
        response_format="content_and_artifact",
    )
    async def search_domain_guidance_tool(
        query: str,
        max_results: int = 5,
    ) -> tuple[str, list[Document]]:
        """Query the domain knowledge base for statutes and practice directions.

        Use this to retrieve applicable statutes, bench books, and procedural
        rules. Do not pass vector_store_id — it is injected automatically.
        Raises DomainGuidanceUnavailable if the domain store is not configured.
        """
        from src.tools.exceptions import DomainGuidanceUnavailable
        from src.tools.search_domain_guidance import search_domain_guidance

        if not vector_store_id:
            raise DomainGuidanceUnavailable("No domain_vector_store_id configured for this case")
        results = await search_domain_guidance(
            query=query,
            vector_store_id=vector_store_id,
            max_results=max_results,
        )
        artifact = [_guidance_to_document(g) for g in results]
        return _format_results_for_llm(results), artifact

    all_tools["search_domain_guidance"] = search_domain_guidance_tool

    # ------------------------------------------------------------------
    # ask_judge  (Q1.11 chat-steering — agent-initiated question to Judge)
    # ------------------------------------------------------------------
    # case_id is captured at tool-registration time. The closure value is
    # stable for the life of this make_tools invocation, which matches the
    # life of one agent run — exactly what we need to attribute the SSE
    # event correctly.
    _ask_judge_case_id = str(state["case"].case_id) if state.get("case") else ""

    @tool("ask_judge", args_schema=_AskJudgeInput)
    async def ask_judge_tool(question: str) -> str:
        """Pause the pipeline and ask the presiding Judge a calibration question.

        The synthesis prompt MANDATES at least one call per phase run —
        every gate-3 review must carry a substantive Judge-only question
        so the gate review surface stays meaningful. The graph pauses
        until the Judge replies via the workspace chat panel; the reply
        is returned as this tool's result so you can incorporate it
        into your reasoning chain.

        Ask about: framing weight, interpretive priorities between two
        legally tenable readings, escalation thresholds, sentencing-band
        emphasis. Do NOT ask: for confirmation, for restatement, for
        facts already in upstream state.
        """
        import uuid as _uuid
        from datetime import UTC as _UTC, datetime as _datetime

        from langgraph.types import interrupt as _interrupt

        from src.services.pipeline_events import publish_agent_event

        # Mint our own id rather than relying on LangGraph's internal interrupt
        # numbering — this id rides on the SSE event and the /respond resume
        # payload, letting the API reject stale double-sends with a 409.
        interrupt_id = _uuid.uuid4().hex

        # Surface the question to the frontend before we pause. The
        # `agent` field is hard-coded for v1 because synthesis is the only
        # phase wired with `ask_judge` (PHASE_TOOL_NAMES["synthesis"]).
        # If/when other phases get the tool, plumb phase_or_scope through
        # make_tools so this attribution stays accurate.
        await publish_agent_event(
            _ask_judge_case_id,
            {
                "kind": "interrupt",
                "case_id": _ask_judge_case_id,
                "agent": "synthesis",
                "question": question,
                "interrupt_id": interrupt_id,
                "ts": _datetime.now(_UTC).isoformat(),
            },
        )

        reply = _interrupt(
            {
                "kind": "ask_judge",
                "question": question,
                "interrupt_id": interrupt_id,
            }
        )
        if isinstance(reply, dict):
            return str(reply.get("text") or "")
        return str(reply or "")

    all_tools["ask_judge"] = ask_judge_tool

    # ------------------------------------------------------------------
    # Filter to the agent's allowed subset
    # ------------------------------------------------------------------
    tools = [t for name, t in all_tools.items() if name in allowed_names]
    return tools, precedent_meta
