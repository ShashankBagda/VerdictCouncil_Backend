"""Audit log response schema."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AuditLogResponse(BaseModel):
    """Full audit log entry."""

    id: UUID = Field(..., description="Audit log entry ID")
    case_id: UUID = Field(..., description="Associated case ID")
    agent_name: str = Field(..., description="Agent that performed the action", examples=["legal-knowledge"])
    action: str = Field(..., description="Action performed", examples=["search_precedents"])
    input_payload: dict | None = Field(None, description="Input data sent to the agent")
    output_payload: dict | None = Field(None, description="Output data from the agent")
    model: str | None = Field(None, description="LLM model used", examples=["gpt-4o-mini"])
    token_usage: dict | None = Field(None, description="Token usage statistics")
    created_at: datetime | None = Field(None, description="Timestamp of the action")

    model_config = {"from_attributes": True}
