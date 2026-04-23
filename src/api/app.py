from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from starlette.routing import Route

from src.api.middleware.metrics import MetricsMiddleware, metrics_endpoint
from src.api.middleware.rate_limit import RateLimitMiddleware
from src.pipeline.observability import configure_mlflow
from src.shared.config import settings

OPENAPI_TAGS = [
    {
        "name": "auth",
        "description": "User registration, login/logout, and session management. "
        "Authentication uses JWT tokens stored in httpOnly cookies.",
    },
    {
        "name": "cases",
        "description": "CRUD operations for judicial cases. Judges create and manage cases; admins have full visibility.",
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
    {
        "name": "hearing-notes",
        "description": "Judge-private hearing annotations: create, list, edit, lock, delete.",
    },
    {
        "name": "hearing-pack",
        "description": (
            "Generate a hearing preparation pack (summary, evidence, arguments, and analysis)."
        ),
    },
    {
        "name": "reopen-requests",
        "description": "Party-initiated reopen workflow: submit, list, and review requests.",
    },
    {
        "name": "senior-inbox",
        "description": "Senior judge inbox: items awaiting review across cases.",
    },
    {
        "name": "admin",
        "description": (
            "Administrative operations: vector store refresh, user actions, cost config."
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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    configure_mlflow()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        lifespan=_lifespan,
        title="VerdictCouncil API",
        version="0.1.0",
        summary="Judicial hearing support system with 9-agent AI analysis pipeline",
        description=(
            "VerdictCouncil processes judicial cases through a multi-agent pipeline "
            "covering case intake, evidence analysis, fact reconstruction, legal knowledge, "
            "argument construction, and hearing analysis.\n\n"
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
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RateLimitMiddleware)

    from src.api.routes import (
        admin,
        audit,
        auth,
        case_data,
        cases,
        dashboard,
        documents,
        health,
        hearing_notes,
        hearing_pack,
        judge,
        knowledge_base,
        precedent_search,
        reopen_requests,
        what_if,
    )

    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(cases.router, prefix="/api/v1/cases", tags=["cases"])
    app.include_router(case_data.router, prefix="/api/v1/cases", tags=["cases"])
    app.include_router(what_if.router, prefix="/api/v1/cases", tags=["what-if"])
    app.include_router(judge.router, prefix="/api/v1/cases", tags=["judge"])
    app.include_router(hearing_notes.router, prefix="/api/v1/cases", tags=["hearing-notes"])
    app.include_router(hearing_pack.router, prefix="/api/v1/cases", tags=["hearing-pack"])
    app.include_router(reopen_requests.router, prefix="/api/v1/cases", tags=["reopen-requests"])
    app.include_router(audit.router, prefix="/api/v1/audit", tags=["audit"])
    app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["dashboard"])
    app.include_router(health.router, prefix="/api/v1/health", tags=["health"])
    app.include_router(
        precedent_search.router, prefix="/api/v1/precedents", tags=["precedent-search"]
    )
    app.include_router(
        knowledge_base.router, prefix="/api/v1/knowledge-base", tags=["knowledge-base"]
    )
    app.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])
    app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])

    # Prometheus-compatible metrics (excluded from OpenAPI spec)
    app.routes.append(Route("/metrics", metrics_endpoint, methods=["GET"], include_in_schema=False))

    return app


app = create_app()
