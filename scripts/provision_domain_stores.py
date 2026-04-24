"""Idempotent provisioner for per-domain OpenAI vector stores.

Run after migration 0019 has been applied and before enabling the domain
intake path. Each domain with vector_store_id IS NULL gets a dedicated
store created and is flipped to is_active=True.

Usage:
    python scripts/provision_domain_stores.py              # provision all pending
    python scripts/provision_domain_stores.py --dry-run    # report what would happen
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import AsyncOpenAI
from sqlalchemy import select, text

from src.models.domain import Domain
from src.services.database import async_session
from src.shared.config import settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")


async def _list_stores_by_domain_code(client: AsyncOpenAI, domain_code: str) -> list[str]:
    """Return vector store IDs already tagged with the given domain code."""
    store_ids = []
    async for store in await client.vector_stores.list():
        meta = store.metadata or {}
        if meta.get("domain_code") == domain_code and meta.get("env") == settings.namespace:
            store_ids.append(store.id)
    return store_ids


async def provision(dry_run: bool = False) -> None:
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    async with async_session() as db:
        result = await db.execute(
            select(Domain).where(Domain.vector_store_id.is_(None)).with_for_update(skip_locked=True)
        )
        pending = list(result.scalars().all())

    if not pending:
        logger.info("All domains already provisioned.")
        return

    for domain in pending:
        logger.info("Processing domain %s (%s)", domain.code, domain.id)
        if dry_run:
            logger.info("  [dry-run] would provision vector store for domain %s", domain.code)
            continue

        async with async_session() as db:
            # Advisory lock keyed on hash of domain code to serialise concurrent runs
            domain_key = hash(domain.code) & 0x7FFFFFFF
            await db.execute(text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=domain_key))

            # Re-read inside the lock to check for concurrent completion
            row = await db.get(Domain, domain.id)
            if row is None:
                logger.warning("Domain %s disappeared; skipping", domain.id)
                continue
            if row.vector_store_id:
                logger.info("Domain %s already provisioned by another worker", domain.code)
                continue

            # Phase 1: check for existing store by metadata tag (adopt if found)
            existing_ids = await _list_stores_by_domain_code(client, domain.code)
            if existing_ids:
                store_id = existing_ids[0]
                logger.info("Adopting existing store %s for domain %s", store_id, domain.code)
            else:
                # Phase 2: record intent before calling OpenAI
                from datetime import UTC, datetime

                row.provisioning_attempts = (row.provisioning_attempts or 0) + 1
                row.provisioning_started_at = datetime.now(UTC)
                await db.flush()

                store = await client.vector_stores.create(
                    name=f"domain-{domain.code}-{settings.namespace}",
                    metadata={"domain_code": domain.code, "env": settings.namespace},
                )
                store_id = store.id
                logger.info("Created store %s for domain %s", store_id, domain.code)

            row.vector_store_id = store_id
            row.is_active = True
            row.provisioning_started_at = None
            await db.commit()
            logger.info("Domain %s provisioned and activated", domain.code)

        if (row.provisioning_attempts or 0) > 3 and not row.vector_store_id:
            logger.error(
                "Domain %s has %d failed provisioning attempts — manual inspection required",
                domain.code,
                row.provisioning_attempts,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision per-domain OpenAI vector stores.")
    parser.add_argument("--dry-run", action="store_true", help="Report without making changes")
    args = parser.parse_args()
    asyncio.run(provision(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
