import ssl

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.shared.config import settings

_async_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")

# asyncpg rejects ?sslmode=require (a libpq/psycopg2 param) with a TypeError.
# sslmode=require means "encrypt but skip cert verification" — replicate with
# an SSLContext that has check_hostname=False and CERT_NONE, matching psycopg
# semantics for this sslmode value.
_connect_args: dict = {}
if "sslmode=require" in _async_url:
    _async_url = _async_url.replace("?sslmode=require", "").replace("&sslmode=require", "")
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE
    _connect_args["ssl"] = _ssl_ctx

engine = create_async_engine(
    _async_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    connect_args=_connect_args,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
