from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.routing import Route

from src.api.middleware.metrics import MetricsMiddleware, metrics_endpoint
from src.api.middleware.rate_limit import RateLimitMiddleware


def create_app() -> FastAPI:
    app = FastAPI(title="VerdictCouncil API", version="0.1.0")

    # Middleware is applied in reverse order (last added runs first).
    # Order of execution: RateLimit -> Metrics -> CORS -> handler
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RateLimitMiddleware)

    from src.api.routes import audit, auth, cases, dashboard, decisions, health, what_if

    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(cases.router, prefix="/api/v1/cases", tags=["cases"])
    app.include_router(decisions.router, prefix="/api/v1/cases", tags=["decisions"])
    app.include_router(what_if.router, prefix="/api/v1/cases", tags=["what-if"])
    app.include_router(audit.router, prefix="/api/v1/audit", tags=["audit"])
    app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["dashboard"])
    app.include_router(health.router, prefix="/api/v1/health", tags=["health"])

    # Prometheus-compatible metrics endpoint
    app.routes.append(Route("/metrics", metrics_endpoint, methods=["GET"]))

    return app


app = create_app()
