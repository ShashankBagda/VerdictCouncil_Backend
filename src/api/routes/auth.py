import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import APIRouter, BackgroundTasks, Cookie, HTTPException, Response, status
from sqlalchemy import select

from src.api.deps import CurrentUser, DBSession
from src.api.schemas.auth import (
    LoginRequest,
    PasswordResetRequestBody,
    PasswordResetVerifyBody,
    RegisterRequest,
    UserResponse,
)
from src.api.schemas.common import ErrorResponse, MessageResponse, ValidationErrorResponse
from src.models.user import PasswordResetToken, Session, User
from src.services.mailer import send_password_reset_email
from src.shared.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

TOKEN_EXPIRY_HOURS = 24


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


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Invalid hash format should behave like invalid credentials.
        return False


def _hash_jwt_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
        password_hash=_hash_password(body.password),
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
    description="Verify credentials and set an httpOnly `vc_token` JWT cookie. The cookie is valid for 24 hours.",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid email or password"},
        422: {"model": ValidationErrorResponse, "description": "Validation error"},
    },
    openapi_extra={"security": []},
)
async def login(body: LoginRequest, response: Response, db: DBSession) -> dict:
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not _verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = _create_token(user)
    expires_at = datetime.now(UTC) + timedelta(hours=TOKEN_EXPIRY_HOURS)
    db.add(
        Session(
            user_id=user.id,
            jwt_token_hash=_hash_jwt_token(token),
            expires_at=expires_at,
        )
    )
    await db.flush()

    response.set_cookie(
        key="vc_token",
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=TOKEN_EXPIRY_HOURS * 3600,
    )
    return {"message": "logged in"}


@router.post(
    "/logout",
    response_model=MessageResponse,
    operation_id="logout",
    summary="Clear session cookie",
    description="Delete the `vc_token` cookie to end the session.",
    openapi_extra={"security": []},
)
async def logout(
    response: Response,
    db: DBSession,
    vc_token: str | None = Cookie(default=None),
) -> dict:
    if vc_token:
        result = await db.execute(select(Session).where(Session.jwt_token_hash == _hash_jwt_token(vc_token)))
        session = result.scalar_one_or_none()
        if session:
            await db.delete(session)

    response.delete_cookie("vc_token")
    return {"message": "logged out"}


@router.get(
    "/session",
    operation_id="get_session",
    summary="Get current auth session",
    description="Returns authenticated user and session expiry.",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
    },
)
async def session_info(
    current_user: CurrentUser,
    db: DBSession,
    vc_token: str | None = Cookie(default=None),
) -> dict:
    if not vc_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    result = await db.execute(select(Session).where(Session.jwt_token_hash == _hash_jwt_token(vc_token)))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked or expired")

    return {
        "user": UserResponse.model_validate(current_user).model_dump(mode="json"),
        "session": {"expires_at": session.expires_at.isoformat()},
        "expires_at": session.expires_at.isoformat(),
    }


@router.post(
    "/extend",
    operation_id="extend_session",
    summary="Extend current auth session",
    description="Rotates the JWT cookie and extends session expiry.",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
    },
)
async def extend_session(
    response: Response,
    current_user: CurrentUser,
    db: DBSession,
    vc_token: str | None = Cookie(default=None),
) -> dict:
    if not vc_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    result = await db.execute(select(Session).where(Session.jwt_token_hash == _hash_jwt_token(vc_token)))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked or expired")

    new_token = _create_token(current_user)
    new_expires_at = datetime.now(UTC) + timedelta(hours=TOKEN_EXPIRY_HOURS)
    session.jwt_token_hash = _hash_jwt_token(new_token)
    session.expires_at = new_expires_at
    await db.flush()

    response.set_cookie(
        key="vc_token",
        value=new_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=TOKEN_EXPIRY_HOURS * 3600,
    )

    return {
        "message": "session extended",
        "user": UserResponse.model_validate(current_user).model_dump(mode="json"),
        "session": {"expires_at": new_expires_at.isoformat()},
        "expires_at": new_expires_at.isoformat(),
    }


@router.post(
    "/request-reset",
    response_model=MessageResponse,
    operation_id="request_password_reset",
    summary="Request a password reset link",
    description="Generate a password reset token and email the link. Always returns 200 "
    "to avoid email enumeration. If SMTP is not configured the link is logged "
    "server-side at WARNING level for manual delivery.",
    openapi_extra={"security": []},
)
async def request_password_reset(
    body: PasswordResetRequestBody,
    background_tasks: BackgroundTasks,
    db: DBSession,
) -> dict:
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user:
        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(minutes=settings.reset_token_ttl_minutes)
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=_hash_jwt_token(raw_token),
                expires_at=expires_at,
            )
        )
        await db.flush()
        background_tasks.add_task(send_password_reset_email, user.email, raw_token)
    else:
        logger.info("password reset requested for unknown email (ignored)")

    return {"message": "If the email is registered, a reset link has been sent"}


@router.post(
    "/verify-reset",
    response_model=MessageResponse,
    operation_id="verify_password_reset",
    summary="Consume a password reset token and set a new password",
    description="Validates the reset token, sets the user's new password, and marks the "
    "token as used. Tokens are single-use and expire after the configured TTL.",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid or expired reset token"},
        422: {"model": ValidationErrorResponse, "description": "Validation error"},
    },
    openapi_extra={"security": []},
)
async def verify_password_reset(body: PasswordResetVerifyBody, db: DBSession) -> dict:
    token_hash = _hash_jwt_token(body.token)
    result = await db.execute(select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash))
    reset_token = result.scalar_one_or_none()

    now = datetime.now(UTC)
    if not reset_token or reset_token.used_at is not None or reset_token.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    user_result = await db.execute(select(User).where(User.id == reset_token.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    user.password_hash = _hash_password(body.new_password)
    reset_token.used_at = now
    await db.flush()

    return {"message": "Password has been reset"}


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
