from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi import APIRouter, HTTPException, Response, status
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from src.api.deps import CurrentUser, DBSession
from src.models.user import User, UserRole
from src.shared.config import settings

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

TOKEN_EXPIRY_HOURS = 24


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: UserRole


class UserResponse(BaseModel):
    id: UUID
    name: str
    email: str
    role: UserRole

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class MessageResponse(BaseModel):
    message: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _create_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "exp": datetime.now(UTC) + timedelta(hours=TOKEN_EXPIRY_HOURS),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: DBSession) -> User:
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        name=body.name,
        email=body.email,
        role=body.role,
        password_hash=pwd_context.hash(body.password),
    )
    db.add(user)
    try:
        await db.flush()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from None
    await db.refresh(user)
    return user


@router.post("/login", response_model=MessageResponse)
async def login(body: LoginRequest, response: Response, db: DBSession) -> dict:
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not pwd_context.verify(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = _create_token(user)
    response.set_cookie(
        key="vc_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=TOKEN_EXPIRY_HOURS * 3600,
    )
    return {"message": "logged in"}


@router.post("/logout", response_model=MessageResponse)
async def logout(response: Response) -> dict:
    response.delete_cookie("vc_token")
    return {"message": "logged out"}


@router.get("/me", response_model=UserResponse)
async def me(current_user: CurrentUser) -> User:
    return current_user
