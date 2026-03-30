from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from src.api.deps import CurrentUser, DBSession
from src.models.case import Case
from src.shared.circuit_breaker import CircuitBreaker

router = APIRouter()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


_pair_breaker = CircuitBreaker(service_name="pair_search")


class DashboardStats(BaseModel):
    total_cases: int
    by_status: dict[str, int]
    by_domain: dict[str, int]
    recent_cases: list[dict]
    pair_api_status: dict | None = None


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get("/stats", response_model=DashboardStats)
async def get_stats(db: DBSession, current_user: CurrentUser) -> dict:
    # Total
    total = (await db.execute(select(func.count(Case.id)))).scalar_one()

    # By status
    status_rows = (
        await db.execute(select(Case.status, func.count(Case.id)).group_by(Case.status))
    ).all()
    by_status = {row[0].value: row[1] for row in status_rows}

    # By domain
    domain_rows = (
        await db.execute(select(Case.domain, func.count(Case.id)).group_by(Case.domain))
    ).all()
    by_domain = {row[0].value: row[1] for row in domain_rows}

    # Recent cases (last 10)
    recent_result = await db.execute(select(Case).order_by(Case.created_at.desc()).limit(10))
    recent = [
        {
            "id": str(c.id),
            "domain": c.domain.value,
            "status": c.status.value,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in recent_result.scalars().all()
    ]

    pair_status = await _pair_breaker.get_status()

    return {
        "total_cases": total,
        "by_status": by_status,
        "by_domain": by_domain,
        "recent_cases": recent,
        "pair_api_status": pair_status,
    }
