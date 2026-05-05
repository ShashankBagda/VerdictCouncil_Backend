from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.shared.config import settings

_async_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")

# asyncpg rejects ?sslmode=require (a libpq/psycopg2 param) with a TypeError.
# Strip it and pass ssl=True via connect_args so asyncpg negotiates TLS.
_connect_args: dict = {}
if "sslmode=require" in _async_url:
    _async_url = _async_url.replace("?sslmode=require", "").replace("&sslmode=require", "")
    _connect_args["ssl"] = True

engine = create_async_engine(
    _async_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    connect_args=_connect_args,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
