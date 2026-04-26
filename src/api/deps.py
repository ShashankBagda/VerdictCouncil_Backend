import hashlib
import logging
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.user import Session, User, UserRole
from src.services.database import async_session
from src.shared.config import settings

_logger = logging.getLogger(__name__)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
            try:
                await session.commit()
            except (PendingRollbackError, DBAPIError) as exc:
                # Long-lived requests (e.g. SSE streams) can outlive their
                # underlying asyncpg connection; commit then fails because
                # the transaction is already invalid. Roll back so the
                # connection returns to the pool clean — pool_pre_ping
                # validates it on the next checkout.
                _logger.warning(
                    "get_db commit failed (%s); rolling back invalid session",
                    type(exc).__name__,
                )
                await session.rollback()
        except Exception:
            await session.rollback()
            raise


DBSession = Annotated[AsyncSession, Depends(get_db)]


def _hash_jwt_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def authenticate_token(
    vc_token: str | None,
    session: AsyncSession,
) -> User:
    """Validate a vc_token cookie against the given DB session and return the User.

    Pure helper — no FastAPI dependency. Lets long-lived endpoints (SSE) do
    auth on a short-lived session of their own choosing instead of holding
    the request-scoped session for the whole response lifetime.
    """
    if not vc_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        payload = jwt.decode(vc_token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        ) from None
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from None

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    result = await session.execute(select(User).where(User.id == UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    session_result = await session.execute(
        select(Session).where(
            Session.user_id == user.id,
            Session.jwt_token_hash == _hash_jwt_token(vc_token),
            Session.expires_at > datetime.now(UTC),
        )
    )
    db_session = session_result.scalar_one_or_none()
    if not db_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session revoked or expired",
        )

    return user


async def get_current_user(
    db: DBSession,
    vc_token: str | None = Cookie(default=None),
) -> User:
    return await authenticate_token(vc_token, db)


async def get_current_user_for_stream(
    vc_token: str | None = Cookie(default=None),
) -> User:
    """Auth dep for streaming endpoints.

    Opens its own short-lived session for the auth lookup so the
    request-scoped `get_db` session isn't held across the SSE response
    lifetime. Without this, the request-scoped asyncpg connection would
    sit idle for minutes and trip a `PendingRollbackError` on cleanup
    when its transaction is invalidated.
    """
    async with async_session() as session:
        return await authenticate_token(vc_token, session)


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentUserForStream = Annotated[User, Depends(get_current_user_for_stream)]


def require_role(*roles: UserRole) -> Callable:
    async def _check(current_user: CurrentUser) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role.value}' not permitted",
            )
        return current_user

    return Depends(_check)
