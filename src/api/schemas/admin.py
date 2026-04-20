from __future__ import annotations

from pydantic import BaseModel, Field

from src.models.user import UserRole


class VectorStoreRefreshRequest(BaseModel):
    store: str | None = Field(default=None, description="Optional store identifier")


class VectorStoreRefreshResponse(BaseModel):
    message: str
    store: str | None = None
    status: str


class UserActionRequest(BaseModel):
    role: UserRole | None = None


class UserActionResponse(BaseModel):
    message: str
    user_id: str
    action: str


class CostConfigRequest(BaseModel):
    prompt_cost_per_1k: float | None = None
    completion_cost_per_1k: float | None = None
    currency: str | None = None
    budget_daily: float | None = None


class CostConfigResponse(BaseModel):
    message: str
    config: dict
