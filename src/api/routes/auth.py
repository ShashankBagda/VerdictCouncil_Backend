import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi import APIRouter, Cookie, HTTPException, Response, status
from passlib.context import CryptContext
from sqlalchemy import select

from src.api.deps import CurrentUser, DBSession
from src.api.schemas.auth import LoginRequest, RegisterRequest, UserResponse
from src.api.schemas.common import ErrorResponse, MessageResponse, ValidationErrorResponse
from src.models.user import Session as UserSession
from src.models.user import User
from src.shared.config import settings

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

TOKEN_EXPIRY_HOURS = 24

_COOKIE_KWARGS: dict[str, object] = {
    "key": "vc_token",
    "httponly": True,
    "samesite": "lax",
}


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


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="register_user",
    summary="Register a new user",
    description="Create a new user account with the specified role. "
    "Returns the created user profile (without password).",
    responses={
        409: {"model": ErrorResponse, "description": "Email already registered"},
        422: {"model": ValidationErrorResponse, "description": "Validation error"},
    },
    openapi_extra={"security": []},
)
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


@router.post(
    "/login",
    response_model=MessageResponse,
    operation_id="login",
    summary="Authenticate and receive session cookie",
    description="Verify credentials and set an httpOnly `vc_token` JWT cookie. "
    "The cookie is valid for 24 hours.",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid email or password"},
        422: {"model": ValidationErrorResponse, "description": "Validation error"},
    },
    openapi_extra={"security": []},
)
async def login(body: LoginRequest, response: Response, db: DBSession) -> dict:
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not pwd_context.verify(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = _create_token(user)

    # Create session record for token revocation support
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    session = UserSession(
        user_id=user.id,
        jwt_token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(hours=TOKEN_EXPIRY_HOURS),
    )
    db.add(session)
    await db.flush()

    response.set_cookie(
        **_COOKIE_KWARGS,
        value=token,
        secure=settings.cookie_secure,
        max_age=TOKEN_EXPIRY_HOURS * 3600,
    )
    return {"message": "logged in"}


@router.post(
    "/logout",
    response_model=MessageResponse,
    operation_id="logout",
    summary="Clear session cookie and revoke token",
    description="Delete the `vc_token` cookie and revoke the session to end the session.",
    openapi_extra={"security": []},
)
async def logout(
    response: Response,
    db: DBSession,
    vc_token: str | None = Cookie(default=None),
) -> dict:
    if vc_token:
        token_hash = hashlib.sha256(vc_token.encode()).hexdigest()
        try:
            payload = jwt.decode(
                vc_token,
                settings.jwt_secret,
                algorithms=["HS256"],
                options={"verify_exp": False},
            )
            uid = payload.get("sub")
        except jwt.InvalidTokenError:
            uid = None
        if uid:
            result = await db.execute(
                select(UserSession).where(
                    UserSession.user_id == UUID(uid),
                    UserSession.jwt_token_hash == token_hash,
                )
            )
            session = result.scalar_one_or_none()
            if session:
                await db.delete(session)

    response.delete_cookie(**_COOKIE_KWARGS, secure=settings.cookie_secure)
    return {"message": "logged out"}


@router.get(
    "/me",
    response_model=UserResponse,
    operation_id="get_current_user",
    summary="Get authenticated user profile",
    description="Returns the profile of the currently authenticated user.",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
    },
)
async def me(current_user: CurrentUser) -> User:
    return current_user
