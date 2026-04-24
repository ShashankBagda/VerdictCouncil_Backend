"""Shared response schemas used across multiple endpoints."""

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Standard error response body."""

    detail: str = Field(..., description="Human-readable error message", examples=["Not found"])


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str = Field(..., description="Status message", examples=["Operation successful"])


class ValidationErrorDetail(BaseModel):
    """Single validation error entry (FastAPI 422 format)."""

    loc: list[str | int] = Field(..., description="Path to the invalid field", examples=[["body", "email"]])
    msg: str = Field(..., description="Validation error message", examples=["field required"])
    type: str = Field(..., description="Error type identifier", examples=["value_error.missing"])


class ValidationErrorResponse(BaseModel):
    """FastAPI validation error response (422 Unprocessable Entity)."""

    detail: list[ValidationErrorDetail] = Field(..., description="List of validation errors")
