"""Initial schema with all 17 tables.

Revision ID: 0001
Revises: None
Create Date: 2026-03-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # Enum types
    # -----------------------------------------------------------------------
    user_role = sa.Enum("judge", "admin", "clerk", name="userrole", create_type=False)
    case_domain = sa.Enum(
        "small_claims", "traffic_violation",
        name="casedomain", create_type=False,
    )
    case_status = sa.Enum(
        "pending", "processing", "ready_for_review", "decided",
        "rejected", "escalated", "closed", "failed",
        name="casestatus", create_type=False,
    )
    case_complexity = sa.Enum("low", "medium", "high", name="casecomplexity", create_type=False)
    case_route = sa.Enum(
        "proceed_automated", "proceed_with_review", "escalate_human",
        name="caseroute", create_type=False,
    )
    party_role = sa.Enum(
        "claimant", "respondent", "accused", "prosecution",
        name="partyrole", create_type=False,
    )
    evidence_type = sa.Enum(
        "documentary", "testimonial", "physical", "digital", "expert",
        name="evidencetype", create_type=False,
    )
    evidence_strength = sa.Enum("strong", "medium", "weak", name="evidencestrength", create_type=False)
    fact_confidence = sa.Enum("high", "medium", "low", "disputed", name="factconfidence", create_type=False)
    fact_status = sa.Enum("agreed", "disputed", name="factstatus", create_type=False)
    precedent_source = sa.Enum("curated", "live_search", name="precedentsource", create_type=False)
    argument_side = sa.Enum(
        "prosecution", "defense", "claimant", "respondent",
        name="argumentside", create_type=False,
    )
    recommendation_type = sa.Enum(
        "compensation", "repair", "dismiss", "guilty", "not_guilty", "reduced",
        name="recommendationtype", create_type=False,
    )
    modification_type = sa.Enum(
        "fact_toggle", "evidence_exclusion", "witness_credibility", "legal_interpretation",
        name="modificationtype", create_type=False,
    )
    scenario_status = sa.Enum(
        "pending", "running", "completed", "failed", "cancelled",
        name="scenariostatus", create_type=False,
    )
    stability_classification = sa.Enum(
        "stable", "moderately_sensitive", "highly_sensitive",
        name="stabilityclassification", create_type=False,
    )
    stability_status = sa.Enum(
        "pending", "computing", "completed", "failed",
        name="stabilitystatus", create_type=False,
    )

    # Create all enum types
    for enum in [
        user_role, case_domain, case_status, case_complexity, case_route,
        party_role, evidence_type, evidence_strength, fact_confidence,
        fact_status, precedent_source, argument_side, recommendation_type,
        modification_type, scenario_status, stability_classification,
        stability_status,
    ]:
        enum.create(op.get_bind(), checkfirst=True)

    # -----------------------------------------------------------------------
    # 1. users
    # -----------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )

    # -----------------------------------------------------------------------
    # 2. sessions
    # -----------------------------------------------------------------------
    op.create_table(
        "sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("jwt_token_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    # -----------------------------------------------------------------------
    # 3. cases
    # -----------------------------------------------------------------------
    op.create_table(
        "cases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("domain", case_domain, nullable=False),
        sa.Column("status", case_status, nullable=False, server_default="pending"),
        sa.Column("jurisdiction_valid", sa.Boolean()),
        sa.Column("complexity", case_complexity),
        sa.Column("route", case_route),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_cases_created_by", "cases", ["created_by"])
    op.create_index("ix_cases_status", "cases", ["status"])

    # -----------------------------------------------------------------------
    # 4. parties
    # -----------------------------------------------------------------------
    op.create_table(
        "parties",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", party_role, nullable=False),
        sa.Column("contact_info", JSONB),
    )
    op.create_index("ix_parties_case_id", "parties", ["case_id"])

    # -----------------------------------------------------------------------
    # 5. documents
    # -----------------------------------------------------------------------
    op.create_table(
        "documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("openai_file_id", sa.String(255)),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_type", sa.String(100)),
        sa.Column("uploaded_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_documents_case_id", "documents", ["case_id"])

    # -----------------------------------------------------------------------
    # 6. evidence
    # -----------------------------------------------------------------------
    op.create_table(
        "evidence",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id")),
        sa.Column("evidence_type", evidence_type, nullable=False),
        sa.Column("strength", evidence_strength),
        sa.Column("admissibility_flags", JSONB),
        sa.Column("linked_claims", JSONB),
    )
    op.create_index("ix_evidence_case_id", "evidence", ["case_id"])

    # -----------------------------------------------------------------------
    # 7. facts
    # -----------------------------------------------------------------------
    op.create_table(
        "facts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_date", sa.Date()),
        sa.Column("event_time", sa.Time()),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source_document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id")),
        sa.Column("confidence", fact_confidence),
        sa.Column("status", fact_status),
        sa.Column("corroboration", JSONB),
    )
    op.create_index("ix_facts_case_id", "facts", ["case_id"])

    # -----------------------------------------------------------------------
    # 8. witnesses
    # -----------------------------------------------------------------------
    op.create_table(
        "witnesses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(255)),
        sa.Column("party_id", UUID(as_uuid=True), sa.ForeignKey("parties.id")),
        sa.Column("credibility_score", sa.Integer()),
        sa.Column("bias_indicators", JSONB),
        sa.Column("simulated_testimony", sa.Text()),
    )
    op.create_index("ix_witnesses_case_id", "witnesses", ["case_id"])

    # -----------------------------------------------------------------------
    # 9. legal_rules
    # -----------------------------------------------------------------------
    op.create_table(
        "legal_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("statute_name", sa.String(255), nullable=False),
        sa.Column("section", sa.String(255)),
        sa.Column("verbatim_text", sa.Text()),
        sa.Column("relevance_score", sa.Float()),
        sa.Column("application", sa.Text()),
    )
    op.create_index("ix_legal_rules_case_id", "legal_rules", ["case_id"])

    # -----------------------------------------------------------------------
    # 10. precedents
    # -----------------------------------------------------------------------
    op.create_table(
        "precedents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("citation", sa.String(255), nullable=False),
        sa.Column("court", sa.String(255)),
        sa.Column("outcome", sa.String(255)),
        sa.Column("reasoning_summary", sa.Text()),
        sa.Column("similarity_score", sa.Float()),
        sa.Column("distinguishing_factors", sa.Text()),
        sa.Column("source", precedent_source),
        sa.Column("url", sa.String(255)),
    )
    op.create_index("ix_precedents_case_id", "precedents", ["case_id"])

    # -----------------------------------------------------------------------
    # 11. arguments
    # -----------------------------------------------------------------------
    op.create_table(
        "arguments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("side", argument_side, nullable=False),
        sa.Column("legal_basis", sa.Text(), nullable=False),
        sa.Column("supporting_evidence", JSONB),
        sa.Column("weaknesses", sa.Text()),
        sa.Column("suggested_questions", JSONB),
    )
    op.create_index("ix_arguments_case_id", "arguments", ["case_id"])

    # -----------------------------------------------------------------------
    # 12. deliberations
    # -----------------------------------------------------------------------
    op.create_table(
        "deliberations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reasoning_chain", JSONB),
        sa.Column("preliminary_conclusion", sa.Text()),
        sa.Column("uncertainty_flags", JSONB),
        sa.Column("confidence_score", sa.Integer()),
    )
    op.create_index("ix_deliberations_case_id", "deliberations", ["case_id"])

    # -----------------------------------------------------------------------
    # 13. verdicts
    # -----------------------------------------------------------------------
    op.create_table(
        "verdicts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recommendation_type", recommendation_type, nullable=False),
        sa.Column("recommended_outcome", sa.Text(), nullable=False),
        sa.Column("sentence", JSONB),
        sa.Column("confidence_score", sa.Integer()),
        sa.Column("alternative_outcomes", JSONB),
        sa.Column("fairness_report", JSONB),
    )
    op.create_index("ix_verdicts_case_id", "verdicts", ["case_id"])

    # -----------------------------------------------------------------------
    # 14. audit_logs
    # -----------------------------------------------------------------------
    op.create_table(
        "audit_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("action", sa.String(255), nullable=False),
        sa.Column("input_payload", JSONB),
        sa.Column("output_payload", JSONB),
        sa.Column("system_prompt", sa.Text()),
        sa.Column("llm_response", JSONB),
        sa.Column("tool_calls", JSONB),
        sa.Column("model", sa.String(100)),
        sa.Column("token_usage", JSONB),
        sa.Column("solace_message_id", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_logs_case_id", "audit_logs", ["case_id"])

    # -----------------------------------------------------------------------
    # 15. what_if_scenarios
    # -----------------------------------------------------------------------
    op.create_table(
        "what_if_scenarios",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_run_id", sa.String(255), nullable=False),
        sa.Column("scenario_run_id", sa.String(255), unique=True, nullable=False),
        sa.Column("modification_type", modification_type, nullable=False),
        sa.Column("modification_description", sa.Text()),
        sa.Column("modification_payload", JSONB),
        sa.Column("status", scenario_status, nullable=False, server_default="pending"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_what_if_scenarios_case_id", "what_if_scenarios", ["case_id"])
    op.create_index("ix_what_if_scenarios_created_by", "what_if_scenarios", ["created_by"])

    # -----------------------------------------------------------------------
    # 16. what_if_verdicts
    # -----------------------------------------------------------------------
    op.create_table(
        "what_if_verdicts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "scenario_id", UUID(as_uuid=True),
            sa.ForeignKey("what_if_scenarios.id", ondelete="CASCADE"),
            unique=True, nullable=False,
        ),
        sa.Column("original_verdict", JSONB),
        sa.Column("modified_verdict", JSONB),
        sa.Column("diff_view", JSONB),
        sa.Column("verdict_changed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_what_if_verdicts_scenario_id", "what_if_verdicts", ["scenario_id"])

    # -----------------------------------------------------------------------
    # 17. stability_scores
    # -----------------------------------------------------------------------
    op.create_table(
        "stability_scores",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("classification", stability_classification, nullable=False),
        sa.Column("perturbation_count", sa.Integer(), nullable=False),
        sa.Column("perturbations_held", sa.Integer(), nullable=False),
        sa.Column("perturbation_details", JSONB),
        sa.Column("status", stability_status, nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_stability_scores_case_id", "stability_scores", ["case_id"])


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_table("stability_scores")
    op.drop_table("what_if_verdicts")
    op.drop_table("what_if_scenarios")
    op.drop_table("audit_logs")
    op.drop_table("verdicts")
    op.drop_table("deliberations")
    op.drop_table("arguments")
    op.drop_table("precedents")
    op.drop_table("legal_rules")
    op.drop_table("witnesses")
    op.drop_table("facts")
    op.drop_table("evidence")
    op.drop_table("documents")
    op.drop_table("parties")
    op.drop_table("cases")
    op.drop_table("sessions")
    op.drop_table("users")

    # Drop enum types
    for enum_name in [
        "stabilitystatus", "stabilityclassification", "scenariostatus",
        "modificationtype", "recommendationtype", "argumentside",
        "precedentsource", "factstatus", "factconfidence",
        "evidencestrength", "evidencetype", "partyrole",
        "caseroute", "casecomplexity", "casestatus",
        "casedomain", "userrole",
    ]:
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
