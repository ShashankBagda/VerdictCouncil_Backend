"""Smoke test: every HTTP path the frontend calls must respond 2xx/expected.

Walks a hard-coded list of (method, path, expected_status) tuples matching the
endpoints `VerdictCouncil_Frontend/src/lib/api.js` uses after contract alignment,
authenticates as the seeded judge, and fails loudly on the first endpoint that
does not match its expected status.

Run against a freshly-seeded database:

    make infra-up seed
    .venv/bin/python -m scripts.smoke_frontend_contract

or via the Makefile target:

    make smoke-contract

Environment:
    SMOKE_BASE_URL   (default: http://localhost:8000)
    SMOKE_EMAIL      (default: judge@verdictcouncil.sg)
    SMOKE_PASSWORD   (default: password)
    SMOKE_CASE_ID    (default: seeded case 10000000-0000-4000-a000-000000000002)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Check:
    method: str
    path: str
    expected: tuple[int, ...] = (200,)
    # Endpoints where 4xx is an expected "nothing to show yet" answer against a
    # seeded-but-unprocessed case (e.g. no verdict recorded) are allowed here.
    allow_404_on_seed: bool = False


def build_checks(case_id: str) -> list[Check]:
    return [
        # Auth
        Check("GET", "/api/v1/auth/me"),
        Check("GET", "/api/v1/auth/session"),
        # Dashboard / workspace
        Check("GET", "/api/v1/dashboard/stats"),
        Check("GET", "/api/v1/cases/?page=1&per_page=5"),
        Check("GET", "/api/v1/escalated-cases/"),
        Check("GET", "/api/v1/senior-inbox/"),
        # Case dossier — read-only GETs for the seeded case.
        Check("GET", f"/api/v1/cases/{case_id}"),
        Check("GET", f"/api/v1/cases/{case_id}/status"),
        Check("GET", f"/api/v1/cases/{case_id}/evidence"),
        Check("GET", f"/api/v1/cases/{case_id}/witnesses"),
        Check("GET", f"/api/v1/cases/{case_id}/arguments"),
        Check("GET", f"/api/v1/cases/{case_id}/precedents"),
        Check("GET", f"/api/v1/cases/{case_id}/statutes"),
        Check("GET", f"/api/v1/cases/{case_id}/deliberation"),
        Check("GET", f"/api/v1/cases/{case_id}/timeline"),
        Check("GET", f"/api/v1/cases/{case_id}/evidence-gaps"),
        Check("GET", f"/api/v1/cases/{case_id}/stability"),
        Check("GET", f"/api/v1/cases/{case_id}/fairness-audit"),
        Check("GET", f"/api/v1/cases/{case_id}/hearing-notes"),
        Check("GET", f"/api/v1/cases/{case_id}/reopen-requests"),
        Check("GET", f"/api/v1/audit/{case_id}/audit"),
        # Verdict + what-if may legitimately 404 before a verdict is recorded.
        Check("GET", f"/api/v1/cases/{case_id}/verdict", allow_404_on_seed=True),
        Check("GET", f"/api/v1/cases/{case_id}/what-if", allow_404_on_seed=True),
        # Health & KB
        Check("GET", "/api/v1/health/pair"),
        Check("GET", "/api/v1/knowledge-base/status"),
    ]


def login(client: httpx.Client, email: str, password: str) -> None:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    resp.raise_for_status()


def run_check(client: httpx.Client, check: Check) -> tuple[bool, str]:
    try:
        resp = client.request(check.method, check.path)
    except httpx.HTTPError as exc:
        return False, f"transport error: {exc!r}"

    accepted = set(check.expected)
    if check.allow_404_on_seed:
        accepted.add(404)

    if resp.status_code in accepted:
        return True, f"{resp.status_code}"
    return False, f"{resp.status_code} {resp.text[:200]}"


def main() -> int:
    base_url = os.environ.get("SMOKE_BASE_URL", "http://localhost:8000")
    email = os.environ.get("SMOKE_EMAIL", "judge@verdictcouncil.sg")
    password = os.environ.get("SMOKE_PASSWORD", "password")
    case_id = os.environ.get("SMOKE_CASE_ID", "10000000-0000-4000-a000-000000000002")

    print(f"Smoke-checking contract against {base_url} (case={case_id})")

    with httpx.Client(base_url=base_url, timeout=15.0) as client:
        try:
            login(client, email, password)
        except httpx.HTTPError as exc:
            print(f"login failed: {exc!r}", file=sys.stderr)
            return 2

        failures: list[tuple[Check, str]] = []
        for check in build_checks(case_id):
            ok, detail = run_check(client, check)
            marker = "OK " if ok else "FAIL"
            print(f"  {marker}  {check.method:5s} {check.path}  -> {detail}")
            if not ok:
                failures.append((check, detail))

    if failures:
        print(f"\n{len(failures)} endpoint(s) did not respond as expected:")
        for check, detail in failures:
            print(f"  {check.method} {check.path}  {detail}")
        return 1

    print("\nAll frontend contract endpoints responded as expected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
