from datetime import UTC, datetime, timedelta
from uuid import UUID

import bcrypt as _bcrypt
import jwt
from fastapi import APIRouter, Cookie, HTTPException, Response, status
from sqlalchemy import select

from src.api.deps import CurrentUser, DBSession, _hash_jwt_token
from src.api.schemas.auth import LoginRequest, RegisterRequest, UserResponse
from src.api.schemas.common import ErrorResponse, MessageResponse, ValidationErrorResponse
from src.models.user import Session, User
from src.shared.config import settings

router = APIRouter()

TOKEN_EXPIRY_HOURS = 24

_COOKIE_KWARGS: dict[str, object] = {
    "key": "vc_token",
    "httponly": True,
    "samesite": "lax",
}


def _hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def _verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


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

    if not user or not _verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = _create_token(user)
    session = Session(
        user_id=user.id,
        jwt_token_hash=_hash_jwt_token(token),
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
    "/extend",
    response_model=MessageResponse,
    operation_id="extend_session",
    summary="Extend session",
    description="Mint a fresh session token and extend the active session by 24 hours.",
)
async def extend_session(
    response: Response,
    db: DBSession,
    current_user: CurrentUser,
    vc_token: str | None = Cookie(default=None),
) -> dict:
    if not vc_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    result = await db.execute(
        select(Session).where(
            Session.user_id == current_user.id,
            Session.jwt_token_hash == _hash_jwt_token(vc_token),
            Session.expires_at > datetime.now(UTC),
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session revoked or expired",
        )

    new_token = _create_token(current_user)
    session.jwt_token_hash = _hash_jwt_token(new_token)
    session.expires_at = datetime.now(UTC) + timedelta(hours=TOKEN_EXPIRY_HOURS)
    await db.flush()

    response.set_cookie(
        **_COOKIE_KWARGS,
        value=new_token,
        secure=settings.cookie_secure,
        max_age=TOKEN_EXPIRY_HOURS * 3600,
    )
    return {"message": "session extended"}


@router.get(
    "/session",
    operation_id="get_session",
    summary="Get active session details",
    description="Return current authenticated user and active session metadata.",
)
async def get_session(
    db: DBSession,
    current_user: CurrentUser,
    vc_token: str | None = Cookie(default=None),
) -> dict:
    if not vc_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    result = await db.execute(
        select(Session).where(
            Session.user_id == current_user.id,
            Session.jwt_token_hash == _hash_jwt_token(vc_token),
            Session.expires_at > datetime.now(UTC),
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session revoked or expired",
        )

    return {
        "user": {
            "id": str(current_user.id),
            "name": current_user.name,
            "email": current_user.email,
            "role": current_user.role.value,
        },
        "session": {
            "id": str(session.id),
            "created_at": session.created_at.isoformat(),
            "expires_at": session.expires_at.isoformat(),
        },
        "expires_at": session.expires_at.isoformat(),
    }


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
        try:
            payload = jwt.decode(
                vc_token,
                settings.jwt_secret,
                algorithms=["HS256"],
                options={"verify_exp": False},
            )
            user_id = payload.get("sub")
            if user_id:
                session_result = await db.execute(
                    select(Session).where(
                        Session.user_id == UUID(user_id),
                        Session.jwt_token_hash == _hash_jwt_token(vc_token),
                    )
                )
                session = session_result.scalar_one_or_none()
                if session:
                    session.expires_at = datetime.now(UTC)
                    await db.flush()
        except jwt.InvalidTokenError:
            pass

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
