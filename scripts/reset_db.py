"""Drop and recreate the dev database schema from SQLAlchemy models.

Bypasses alembic migrations (which have a SQLAlchemy 2.x enum-creation bug)
and builds the schema directly from Base.metadata, then stamps alembic to head
so future incremental migrations work normally.

Usage:
    python -m scripts.reset_db           # wipes schema, recreates, stamps head
    python -m scripts.reset_db --no-stamp  # skip alembic stamp (for debugging)
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]


def reset():
    no_stamp = "--no-stamp" in sys.argv

    engine = create_engine(DATABASE_URL)

    print("==> Dropping public schema (wipes all tables and types)...")
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO current_user"))

    # Import all models so their metadata is registered on Base.
    # Order matters only to satisfy FK references — SQLAlchemy sorts for us.
    from src.models import Base  # noqa: F401 — registers all model metadata
    import src.models.admin_event  # noqa: F401
    import src.models.audit  # noqa: F401
    import src.models.calibration  # noqa: F401
    import src.models.case  # noqa: F401
    import src.models.pipeline_job  # noqa: F401
    import src.models.system_config  # noqa: F401
    import src.models.user  # noqa: F401
    import src.models.what_if  # noqa: F401

    print("==> Creating schema from models (Base.metadata.create_all)...")
    Base.metadata.create_all(engine)

    print("==> Schema created. Tables:")
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        ).fetchall()
    for row in rows:
        print(f"      {row[0]}")

    if not no_stamp:
        print("==> Stamping alembic version to head...")
        subprocess.run(
            [sys.executable, "-m", "alembic", "stamp", "head"],
            check=True,
        )
        print("==> Done. DB is clean and alembic is at head.")
    else:
        print("==> Done (alembic stamp skipped).")


if __name__ == "__main__":
    reset()
