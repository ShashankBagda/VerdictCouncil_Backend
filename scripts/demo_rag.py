"""Demo: hit the per-domain RAG path end-to-end.

Looks up the OpenAI vector store ID for a given domain code, runs
`search_domain_guidance` against it, and prints the results table-style
so the retrieval is reproducible without spinning up the full pipeline.

Usage:
    python -m scripts.demo_rag <domain_code> "<query>" [--max N]

Example:
    python -m scripts.demo_rag traffic_violation "what constitutes speeding on Sentosa"
    python -m scripts.demo_rag small_claims "limit on consumer claims" --max 3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.models.domain import Domain  # noqa: E402
from src.tools.exceptions import DomainGuidanceUnavailable  # noqa: E402
from src.tools.search_domain_guidance import search_domain_guidance  # noqa: E402

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://vc_dev:vc_dev_password@localhost:5432/verdictcouncil",
)


def _resolve_vector_store(domain_code: str) -> tuple[str, str]:
    """Return (domain_name, vector_store_id) for an active domain row."""
    engine = create_engine(DATABASE_URL.replace("+asyncpg", ""))
    try:
        with Session(engine) as session:
            row = session.execute(
                select(Domain).where(Domain.code == domain_code)
            ).scalar_one_or_none()
            if row is None:
                raise SystemExit(f"unknown domain code: {domain_code!r}")
            if not row.is_active:
                raise SystemExit(f"domain {domain_code!r} is inactive")
            if not row.vector_store_id:
                raise SystemExit(
                    f"domain {domain_code!r} has no provisioned vector_store_id"
                )
            return row.name, row.vector_store_id
    finally:
        engine.dispose()


async def _run(domain_code: str, query: str, max_results: int) -> int:
    name, vector_store_id = _resolve_vector_store(domain_code)
    print(f"domain        : {name} ({domain_code})")
    print(f"vector store  : {vector_store_id}")
    print(f"query         : {query}")
    print(f"max_results   : {max_results}")
    print()

    try:
        results = await search_domain_guidance(
            query=query,
            vector_store_id=vector_store_id,
            max_results=max_results,
        )
    except DomainGuidanceUnavailable as exc:
        print(f"RAG unavailable: {exc}")
        return 2

    if not results:
        print("(no results)")
        return 0

    for i, r in enumerate(results, 1):
        score = r.get("score") or 0
        citation = r.get("citation") or "Unknown"
        snippet = (r.get("content") or "").strip().replace("\n", " ")
        if len(snippet) > 240:
            snippet = snippet[:237] + "..."
        print(f"[{i}] score={score:.3f}  {citation}")
        print(f"    {snippet}")
        print()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("domain", help="Domain code (e.g. traffic_violation, small_claims)")
    parser.add_argument("query", help="Free-text query to retrieve guidance for")
    parser.add_argument("--max", type=int, default=5, help="Max results to return (default: 5)")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.domain, args.query, args.max)))


if __name__ == "__main__":
    main()
