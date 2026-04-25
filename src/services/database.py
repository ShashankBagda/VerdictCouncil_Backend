from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.shared.config import settings

_async_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")

engine = create_async_engine(_async_url, echo=False, pool_size=10, max_overflow=20, pool_pre_ping=True)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
