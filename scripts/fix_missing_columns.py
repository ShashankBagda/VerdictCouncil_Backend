"""One-time fix: add columns that were missed due to alembic stamp head."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/verdictcouncil"
)

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    conn.execute(text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "knowledge_base_vector_store_id VARCHAR(255) NULL"
    ))
    conn.commit()
    print("Done: knowledge_base_vector_store_id column ensured.")
