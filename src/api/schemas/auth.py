"""Authentication request/response schemas."""

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from src.models.user import UserRole


class RegisterRequest(BaseModel):
    """User registration request."""

    name: str = Field(
        ..., min_length=1, max_length=255, description="Full name", examples=["Judge Maria Santos"]
    )
    email: EmailStr = Field(
        ..., description="User email address", examples=["maria.santos@court.gov"]
    )
    password: str = Field(..., min_length=8, description="Password (min 8 characters)")
    role: UserRole = Field(..., description="User role in the system", examples=["judge"])


class LoginRequest(BaseModel):
    """User login request."""

    email: EmailStr = Field(
        ..., description="Registered email", examples=["maria.santos@court.gov"]
    )
    password: str = Field(..., description="Account password")


class PasswordResetRequest(BaseModel):
    """Request password reset token via email."""

    email: EmailStr = Field(
        ..., description="Registered email", examples=["maria.santos@court.gov"]
    )


class PasswordResetVerifyRequest(BaseModel):
    """Verify reset token and set a new password."""

    token: str = Field(..., min_length=20, description="Reset token")
    new_password: str = Field(..., min_length=8, description="New password")


class UserResponse(BaseModel):
    """User profile response (excludes sensitive fields)."""

    id: UUID = Field(..., description="User ID")
    name: str = Field(..., description="Full name", examples=["Judge Maria Santos"])
    email: str = Field(..., description="Email address", examples=["maria.santos@court.gov"])
    role: UserRole = Field(..., description="User role", examples=["judge"])

    model_config = {"from_attributes": True}
