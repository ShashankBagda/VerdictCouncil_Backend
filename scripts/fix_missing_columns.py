"""Fix columns/tables missed due to alembic stamp head bypassing migrations 0010-0019."""
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

FIXES = [
    # 0010 – per-judge knowledge base
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS knowledge_base_vector_store_id VARCHAR(255) NULL",
    # 0011 – case intake metadata
    "ALTER TABLE cases ADD COLUMN IF NOT EXISTS title VARCHAR(255) NULL",
    "ALTER TABLE cases ADD COLUMN IF NOT EXISTS filed_date DATE NULL",
    "ALTER TABLE cases ADD COLUMN IF NOT EXISTS claim_amount FLOAT NULL",
    "ALTER TABLE cases ADD COLUMN IF NOT EXISTS consent_to_higher_claim_limit BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE cases ADD COLUMN IF NOT EXISTS offence_code VARCHAR(100) NULL",
    "CREATE INDEX IF NOT EXISTS ix_cases_filed_date ON cases (filed_date)",
    "CREATE INDEX IF NOT EXISTS ix_cases_offence_code ON cases (offence_code)",
    # 0012 – latest_run_id
    "ALTER TABLE cases ADD COLUMN IF NOT EXISTS latest_run_id VARCHAR(36) NULL",
    # 0017 – gate model columns
    "ALTER TABLE cases ADD COLUMN IF NOT EXISTS gate_state JSONB NULL",
    "ALTER TABLE cases ADD COLUMN IF NOT EXISTS judicial_decision JSONB NULL",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS pages JSONB NULL",
    # 0019 – domain FK on cases (domains table created by migration; FK added only if table exists)
    """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='domains')
        AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='cases' AND column_name='domain_id'
        ) THEN
            ALTER TABLE cases ADD COLUMN domain_id UUID REFERENCES domains(id) NULL;
        END IF;
    END $$""",
]

with engine.connect() as conn:
    for sql in FIXES:
        try:
            conn.execute(text(sql))
            conn.commit()
            label = sql.strip().split('\n')[0][:80]
            print(f"OK: {label}")
        except Exception as e:
            conn.rollback()
            print(f"SKIP ({e.__class__.__name__}): {sql.strip()[:60]}")

print("\nAll fixes applied.")
