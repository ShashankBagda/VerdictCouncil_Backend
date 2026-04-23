"""Remove clerk and senior_judge from userrole enum (judge + admin only).

Revision ID: 0018
Revises: 0017
Create Date: 2026-04-23
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reassign any legacy clerk/senior_judge rows to judge before dropping those values.
    op.execute("UPDATE users SET role = 'judge' WHERE role IN ('clerk', 'senior_judge')")
    # PostgreSQL doesn't support DROP VALUE on enums directly.
    # Rename old type, create new type with only judge/admin, migrate column, drop old type.
    op.execute("ALTER TYPE userrole RENAME TO userrole_old")
    op.execute("CREATE TYPE userrole AS ENUM ('judge', 'admin')")
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE userrole "
        "USING role::text::userrole"
    )
    op.execute("DROP TYPE userrole_old")


def downgrade() -> None:
    op.execute("ALTER TYPE userrole RENAME TO userrole_new")
    op.execute("CREATE TYPE userrole AS ENUM ('judge', 'admin', 'clerk', 'senior_judge')")
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE userrole "
        "USING role::text::userrole"
    )
    op.execute("DROP TYPE userrole_new")
