from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from src.api.deps import DBSession, require_role
from src.models.audit import AuditLog
from src.models.user import User, UserRole

router = APIRouter()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class AuditLogResponse(BaseModel):
    id: UUID
    case_id: UUID
    agent_name: str
    action: str
    input_payload: dict | None = None
    output_payload: dict | None = None
    model: str | None = None
    token_usage: dict | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get("/{case_id}/audit", response_model=list[AuditLogResponse])
async def list_audit_logs(
    case_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge, UserRole.admin),
    agent_name: str | None = None,
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
) -> list[AuditLog]:
    query = select(AuditLog).where(AuditLog.case_id == case_id)

    if agent_name:
        query = query.where(AuditLog.agent_name == agent_name)
    if from_time:
        query = query.where(AuditLog.created_at >= from_time)
    if to_time:
        query = query.where(AuditLog.created_at <= to_time)

    query = query.order_by(AuditLog.created_at.asc())

    result = await db.execute(query)
    return list(result.scalars().all())
