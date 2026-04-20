from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from starlette.routing import Route

from src.api.middleware.metrics import MetricsMiddleware, metrics_endpoint
from src.api.middleware.rate_limit import RateLimitMiddleware

OPENAPI_TAGS = [
    {
        "name": "auth",
        "description": "User registration, login/logout, and session management. "
        "Authentication uses JWT tokens stored in httpOnly cookies.",
    },
    {
        "name": "cases",
        "description": "CRUD operations for judicial cases. "
        "Role-based access: clerks and judges create cases; admins see all.",
    },
    {
        "name": "decisions",
        "description": "Judge decision recording (accept, modify, or reject). "
        "Cases must be in `ready_for_review` status.",
    },
    {
        "name": "what-if",
        "description": "Contestable Judgment Mode: submit hypothetical case modifications "
        "and measure verdict stability via perturbation analysis.",
    },
    {
        "name": "audit",
        "description": "Immutable audit trail of all agent actions on a case. "
        "Filterable by agent name and time range.",
    },
    {
        "name": "dashboard",
        "description": "Aggregate case statistics, status breakdowns, and system health overview.",
    },
    {
        "name": "health",
        "description": (
            "PAIR API circuit breaker status and active probing for external service health."
        ),
    },
]


def _custom_openapi(app: FastAPI) -> dict:
    """Build enriched OpenAPI schema with security and global responses."""
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        summary=app.summary,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
        servers=app.servers,
    )

    # Cookie-based JWT auth security scheme
    schema.setdefault("components", {})["securitySchemes"] = {
        "cookieAuth": {
            "type": "apiKey",
            "in": "cookie",
            "name": "vc_token",
            "description": "JWT token set via httpOnly cookie on login. "
            "Call POST /api/v1/auth/login to obtain the cookie.",
        }
    }
    schema["security"] = [{"cookieAuth": []}]

    # Inject global 429 response (rate limiter applies to all endpoints)
    error_429 = {
        "description": "Rate limit exceeded (60 requests/minute per client IP)",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
        "headers": {
            "Retry-After": {
                "description": "Seconds until the rate limit resets",
                "schema": {"type": "integer"},
            }
        },
    }
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict) and "responses" in operation:
                operation["responses"]["429"] = error_429

    app.openapi_schema = schema
    return schema


def create_app() -> FastAPI:
    app = FastAPI(
        title="VerdictCouncil API",
        version="0.1.0",
        summary="Judicial AI decision-support system with 9-agent pipeline",
        description=(
            "VerdictCouncil processes judicial cases through a multi-agent pipeline "
            "covering case intake, evidence analysis, fact reconstruction, legal knowledge, "
            "argument construction, deliberation, and verdict generation.\n\n"
            "**Authentication:** Cookie-based JWT. Call `POST /api/v1/auth/login` "
            "to receive an httpOnly `vc_token` cookie.\n\n"
            "**Note:** Swagger UI does not support cookie-based auth for interactive "
            "testing. Use a separate HTTP client (curl, httpx) for authenticated requests."
        ),
        contact={"name": "VerdictCouncil Team"},
        openapi_tags=OPENAPI_TAGS,
        servers=[{"url": "http://localhost:8000", "description": "Local development"}],
    )

    # Override OpenAPI schema generation
    app.openapi = lambda: _custom_openapi(app)  # type: ignore[method-assign]

    # Middleware is applied in reverse order (last added runs first).
    # Order of execution: RateLimit -> Metrics -> CORS -> handler
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RateLimitMiddleware)

    from src.api.routes import (
        audit,
        auth,
        case_data,
        cases,
        dashboard,
        decisions,
        escalation,
        health,
        hearing_pack,
        judge,
        knowledge_base,
        precedent_search,
        what_if,
    )

    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(cases.router, prefix="/api/v1/cases", tags=["cases"])
    app.include_router(case_data.router, prefix="/api/v1/cases", tags=["cases"])
    app.include_router(hearing_pack.router, prefix="/api/v1/cases", tags=["hearing-pack"])
    app.include_router(decisions.router, prefix="/api/v1/cases", tags=["decisions"])
    app.include_router(what_if.router, prefix="/api/v1/cases", tags=["what-if"])
    app.include_router(judge.router, prefix="/api/v1/cases", tags=["judge"])
    app.include_router(audit.router, prefix="/api/v1/audit", tags=["audit"])
    app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["dashboard"])
    app.include_router(health.router, prefix="/api/v1/health", tags=["health"])
    app.include_router(
        precedent_search.router, prefix="/api/v1/precedents", tags=["precedent-search"]
    )
    app.include_router(
        knowledge_base.router, prefix="/api/v1/knowledge-base", tags=["knowledge-base"]
    )
    app.include_router(escalation.router, prefix="/api/v1/escalated-cases", tags=["escalation"])

    # Prometheus-compatible metrics (excluded from OpenAPI spec)
    app.routes.append(Route("/metrics", metrics_endpoint, methods=["GET"], include_in_schema=False))

    return app


app = create_app()
