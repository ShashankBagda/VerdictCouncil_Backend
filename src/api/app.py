from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def create_app() -> FastAPI:
    app = FastAPI(title="VerdictCouncil API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from src.api.routes import audit, auth, cases, dashboard, decisions, what_if

    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(cases.router, prefix="/api/v1/cases", tags=["cases"])
    app.include_router(decisions.router, prefix="/api/v1/cases", tags=["decisions"])
    app.include_router(what_if.router, prefix="/api/v1/cases", tags=["what-if"])
    app.include_router(audit.router, prefix="/api/v1/audit", tags=["audit"])
    app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["dashboard"])

    return app


app = create_app()
