from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from src.api.deps import DBSession, require_role
from src.api.schemas.admin import (
    CostConfigRequest,
    CostConfigResponse,
    UserActionRequest,
    UserActionResponse,
    VectorStoreRefreshRequest,
    VectorStoreRefreshResponse,
)
from src.api.schemas.common import ErrorResponse
from src.models.user import Session, User, UserRole
from src.shared.config import settings

router = APIRouter()


def _admin_storage_dir() -> Path:
    path = Path(settings.admin_storage_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post(
    "/vector-stores/refresh",
    response_model=VectorStoreRefreshResponse,
    operation_id="refresh_vector_store",
    summary="Refresh vector store",
    description="Trigger a vector store refresh workflow marker.",
    responses={403: {"model": ErrorResponse, "description": "Insufficient permissions"}},
)
async def refresh_vector_store(
    body: VectorStoreRefreshRequest,
    current_user: User = require_role(UserRole.admin),
) -> dict:
    store = body.store or settings.openai_vector_store_id or "default"
    marker = {
        "store": store,
        "requested_by": str(current_user.id),
        "requested_at": datetime.now(UTC).isoformat(),
    }
    (_admin_storage_dir() / "vector_store_refresh.json").write_text(
        json.dumps(marker, indent=2), encoding="utf-8"
    )
    return {
        "message": "Vector store refresh request recorded",
        "store": store,
        "status": "queued",
    }


@router.post(
    "/users/{user_id}/{action}",
    response_model=UserActionResponse,
    operation_id="manage_user_action",
    summary="Execute admin action on a user",
    description="Supported actions: set-role, revoke-sessions.",
    responses={
        400: {"model": ErrorResponse, "description": "Unsupported action"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "User not found"},
    },
)
async def manage_user_action(
    user_id: UUID,
    action: str,
    body: UserActionRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.admin),
) -> dict:
    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    normalized_action = action.strip().lower()

    if normalized_action == "set-role":
        if body.role is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="role is required for set-role action",
            )
        target_user.role = body.role
        await db.flush()
        return {
            "message": "User role updated",
            "user_id": str(target_user.id),
            "action": normalized_action,
        }

    if normalized_action == "revoke-sessions":
        sessions_result = await db.execute(select(Session).where(Session.user_id == target_user.id))
        sessions = sessions_result.scalars().all()
        now = datetime.now(UTC)
        for session in sessions:
            session.expires_at = now
        await db.flush()
        return {
            "message": "User sessions revoked",
            "user_id": str(target_user.id),
            "action": normalized_action,
        }

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported action. Use set-role or revoke-sessions.",
    )


@router.post(
    "/cost-config",
    response_model=CostConfigResponse,
    operation_id="set_cost_config",
    summary="Set operational cost configuration",
    responses={403: {"model": ErrorResponse, "description": "Insufficient permissions"}},
)
async def set_cost_config(
    body: CostConfigRequest,
    current_user: User = require_role(UserRole.admin),
) -> dict:
    config = body.model_dump(exclude_none=True)
    config["updated_by"] = str(current_user.id)
    config["updated_at"] = datetime.now(UTC).isoformat()

    (_admin_storage_dir() / "cost_config.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )

    return {
        "message": "Cost configuration updated",
        "config": config,
    }
