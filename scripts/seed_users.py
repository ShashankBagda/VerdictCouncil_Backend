"""Seed the two demo login accounts (judge + admin).

The frontend has no signup page, so a fresh database has no way to log in.
This script idempotently creates the two demo users that `dev.sh` and
`scripts/fix_demo_data.py` both depend on. Demo cases, parties, documents,
and evidence live in `scripts/fix_demo_data.py` — this script is users only.

The fixed UUIDs below match `JUDGE_ID` / `ADMIN_ID` in fix_demo_data.py so
the `created_by` foreign keys resolve.

Usage: python -m scripts.seed_users
"""

import os
import sys
import uuid

import bcrypt
from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.models.user import User, UserRole  # noqa: E402

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://vc_dev:vc_dev_password@localhost:5432/verdictcouncil"
)

JUDGE_ID = uuid.UUID("00000000-0000-4000-a000-000000000001")
ADMIN_ID = uuid.UUID("00000000-0000-4000-a000-000000000002")

DEMO_USERS = [
    (JUDGE_ID, "Judge Sarah Chen", "judge@verdictcouncil.sg", UserRole.judge, "password"),
    (ADMIN_ID, "Admin Marcus Lee", "admin@verdictcouncil.sg", UserRole.admin, "admin123"),
]


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def seed() -> None:
    engine = create_engine(DATABASE_URL)
    with Session(engine) as session:
        inserted = 0
        updated = 0
        for user_id, name, email, role, password in DEMO_USERS:
            # Look up by primary key first — a prior seed (or an older
            # version of this script) may have inserted the canonical UUID
            # under a different email, in which case checking by email
            # alone misses it and the ID-keyed INSERT collides.
            existing = session.get(User, user_id)
            if existing is None:
                # Fall back to the email lookup so we don't double-create
                # a user that exists under a non-canonical UUID.
                existing = session.execute(
                    select(User).where(User.email == email)
                ).scalar_one_or_none()

            if existing is None:
                session.add(
                    User(
                        id=user_id,
                        name=name,
                        email=email,
                        role=role,
                        password_hash=_hash(password),
                    )
                )
                inserted += 1
                continue

            # Refresh the canonical fields so dev.sh always lands the demo
            # accounts in a known-good state. Hash is recomputed only when
            # the password changed, since bcrypt salts make every call
            # produce a different digest and we don't want to invalidate
            # an already-correct credential on every dev.sh run.
            changed = False
            if existing.email != email:
                existing.email = email
                changed = True
            if existing.name != name:
                existing.name = name
                changed = True
            if existing.role != role:
                existing.role = role
                changed = True
            if not bcrypt.checkpw(password.encode("utf-8"), existing.password_hash.encode("utf-8")):
                existing.password_hash = _hash(password)
                changed = True
            if changed:
                updated += 1
        session.commit()
        unchanged = len(DEMO_USERS) - inserted - updated
        print(f"Seed users: {inserted} inserted, {updated} updated, {unchanged} already current.")


if __name__ == "__main__":
    seed()
