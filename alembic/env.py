import os
from logging.config import fileConfig

import sqlalchemy as sa
from alembic import context
from sqlalchemy import engine_from_config, pool

from src.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Override URL from environment if available
database_url = os.environ.get("DATABASE_URL")
if database_url:
    # alembic uses a sync engine; strip the asyncpg driver prefix if present
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    config.set_main_option("sqlalchemy.url", database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # AUTOCOMMIT: ALTER TYPE ADD VALUE is non-transactional in PostgreSQL.
        # Running in autocommit ensures each migration's type changes are
        # committed before the next migration runs, preventing
        # UnsafeNewEnumValueUsage when a later migration references an enum
        # whose values were added by an earlier migration in the same session.
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        # SET lock_timeout is a session-level directive; it persists for the
        # life of the connection across autocommit statements.
        connection.execute(sa.text("SET lock_timeout = '30s'"))
        context.configure(connection=connection, target_metadata=target_metadata)
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
