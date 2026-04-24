"""Seed the database with sample data for development and demo.

Usage: python -m scripts.seed_data
"""

import os
import sys
import uuid
from datetime import UTC, datetime

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# Ensure project root is on sys.path so `src.*` imports resolve.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.models.base import Base  # noqa: E402
from src.models.case import (  # noqa: E402
    Case,
    CaseComplexity,
    CaseDomain,
    CaseRoute,
    CaseStatus,
    Document,
    Evidence,
    EvidenceStrength,
    EvidenceType,
    Party,
    PartyRole,
)
from src.models.user import User, UserRole  # noqa: E402

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/verdictcouncil"
)


def _hash_password(password: str) -> str:
    """Hash password using bcrypt directly (avoids passlib version conflicts)."""
    import bcrypt

    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def seed() -> None:
    engine = create_engine(DATABASE_URL, echo=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        # Check if already seeded
        from sqlalchemy import select
        existing = session.execute(select(User).limit(1)).scalar_one_or_none()
        if existing:
            print("Database already seeded — skipping.")
            return

        # ------------------------------------------------------------------ Users
        judge = User(
            id=uuid.UUID("00000000-0000-4000-a000-000000000001"),
            name="Judge Sarah Chen",
            email="judge@verdictcouncil.sg",
            role=UserRole.judge,
            password_hash=_hash_password("password"),
        )
        admin = User(
            id=uuid.UUID("00000000-0000-4000-a000-000000000002"),
            name="Admin Marcus Lee",
            email="admin@verdictcouncil.sg",
            role=UserRole.admin,
            password_hash=_hash_password("admin123"),
        )

        session.add_all([judge, admin])
        session.flush()

        # ------------------------------------------------------------------ Cases
        case_traffic = Case(
            id=uuid.UUID("10000000-0000-4000-a000-000000000001"),
            domain=CaseDomain.traffic_violation,
            status=CaseStatus.pending,
            jurisdiction_valid=True,
            complexity=CaseComplexity.low,
            route=CaseRoute.proceed_automated,
            created_by=judge.id,
        )
        case_small_claims_1 = Case(
            id=uuid.UUID("10000000-0000-4000-a000-000000000002"),
            domain=CaseDomain.small_claims,
            status=CaseStatus.processing,
            jurisdiction_valid=True,
            complexity=CaseComplexity.medium,
            route=CaseRoute.proceed_with_review,
            created_by=judge.id,
        )
        case_small_claims_2 = Case(
            id=uuid.UUID("10000000-0000-4000-a000-000000000003"),
            domain=CaseDomain.small_claims,
            status=CaseStatus.closed,
            jurisdiction_valid=True,
            complexity=CaseComplexity.low,
            route=CaseRoute.proceed_automated,
            created_by=judge.id,
        )

        session.add_all([case_traffic, case_small_claims_1, case_small_claims_2])
        session.flush()

        # ------------------------------------------------------------------ Parties
        parties = [
            # Traffic case
            Party(case_id=case_traffic.id, name="State Prosecutor", role=PartyRole.prosecution),
            Party(case_id=case_traffic.id, name="John Doe", role=PartyRole.accused),
            # Small claims 1
            Party(case_id=case_small_claims_1.id, name="Alice Tan", role=PartyRole.claimant),
            Party(
                case_id=case_small_claims_1.id,
                name="Bob's Auto Repair",
                role=PartyRole.respondent,
            ),
            # Small claims 2
            Party(
                case_id=case_small_claims_2.id,
                name="Carol Ng",
                role=PartyRole.claimant,
            ),
            Party(
                case_id=case_small_claims_2.id,
                name="Delta Movers Pte Ltd",
                role=PartyRole.respondent,
            ),
        ]
        session.add_all(parties)
        session.flush()

        # ------------------------------------------------------------------ Documents
        doc_traffic = Document(
            case_id=case_traffic.id,
            filename="traffic_violation_report.pdf",
            file_type="application/pdf",
            uploaded_by=clerk.id,
            uploaded_at=datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
        )
        doc_sc1 = Document(
            case_id=case_small_claims_1.id,
            filename="repair_invoice.pdf",
            file_type="application/pdf",
            uploaded_by=clerk.id,
            uploaded_at=datetime(2026, 2, 10, 14, 30, tzinfo=UTC),
        )
        doc_sc2 = Document(
            case_id=case_small_claims_2.id,
            filename="moving_contract.pdf",
            file_type="application/pdf",
            uploaded_by=admin.id,
            uploaded_at=datetime(2026, 3, 1, 8, 0, tzinfo=UTC),
        )
        session.add_all([doc_traffic, doc_sc1, doc_sc2])
        session.flush()

        # ------------------------------------------------------------------ Evidence
        evidence_items = [
            Evidence(
                case_id=case_traffic.id,
                document_id=doc_traffic.id,
                evidence_type=EvidenceType.documentary,
                strength=EvidenceStrength.strong,
                admissibility_flags={"authenticated": True},
                linked_claims={"charge": "speeding"},
            ),
            Evidence(
                case_id=case_small_claims_1.id,
                document_id=doc_sc1.id,
                evidence_type=EvidenceType.documentary,
                strength=EvidenceStrength.medium,
                admissibility_flags={"authenticated": True},
                linked_claims={"claim": "overcharging for repairs"},
            ),
            Evidence(
                case_id=case_small_claims_2.id,
                document_id=doc_sc2.id,
                evidence_type=EvidenceType.documentary,
                strength=EvidenceStrength.strong,
                admissibility_flags={"authenticated": True},
                linked_claims={"claim": "damaged goods during move"},
            ),
        ]
        session.add_all(evidence_items)

        session.commit()
        print("\nSeed data inserted successfully.")
        print(f"  Users:     {judge.name}, {admin.name}")
        print(f"  Cases:     {case_traffic.id}, {case_small_claims_1.id}, {case_small_claims_2.id}")
        print(f"  Parties:   {len(parties)}")
        print("  Documents: 3")
        print(f"  Evidence:  {len(evidence_items)}")


if __name__ == "__main__":
    seed()
