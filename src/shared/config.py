import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Required
    openai_api_key: str = ""
    database_url: str = "postgresql://vc_dev:vc_dev_password@localhost:5432/verdictcouncil"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "change-me-in-production"  # validated at startup
    cookie_secure: bool = True
    reset_token_ttl_minutes: int = 30
    password_reset_base_url: str = "http://localhost:5173/reset-password"

    # Comma-separated list of origins permitted by CORS. Use `cors_origins_list`
    # to read the parsed form.
    frontend_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.frontend_origins.split(",") if o.strip()]

    # Optional SMTP config for password reset delivery.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = "no-reply@verdictcouncil.local"

    # Per-judge knowledge base upload limits (bytes). 25MB default keeps us
    # under the 50MB ingress limit in k8s/base/ingress.yaml.
    kb_max_upload_bytes: int = 26214400
    # Per-domain KB upload limit (bytes). 50MB default.
    domain_kb_max_upload_bytes: int = 52428800
    # Per-case document upload limit (bytes). 50MB default.
    case_doc_max_upload_bytes: int = 52428800
    # llm-guard DeBERTa-v3 classifier is now wired in on top of the regex pre-filter.
    # Upload route is open; both flags default True in production.
    # Set DOMAIN_UPLOADS_ENABLED=false or CLASSIFIER_SANITIZER_ENABLED=false in .env to override.
    domain_uploads_enabled: bool = True
    # When True, classify_text_async() in sanitization.py runs the DeBERTa-v3 classifier
    # on each document page during admin KB ingest (parse_document run_classifier=True path).
    classifier_sanitizer_enabled: bool = True

    def model_post_init(self, __context: object) -> None:
        import logging
        import warnings

        if self.jwt_secret == "change-me-in-production":
            warnings.warn(
                "JWT_SECRET is using the default value. Set a secure secret via the JWT_SECRET environment variable.",  # noqa: E501
                stacklevel=2,
            )
        if not self.cookie_secure:
            warnings.warn(
                "COOKIE_SECURE is False. Session cookies will be sent over "
                "insecure HTTP. This must ONLY be used in local development.",
                stacklevel=2,
            )
        if self.pair_api_key is None:
            logging.getLogger(__name__).info(
                "PAIR disabled — no PAIR_API_KEY configured; vector store "
                "is the primary precedent source."
            )

    # Application
    namespace: str = "verdictcouncil"
    fastapi_host: str = "0.0.0.0"  # nosec B104 — intentional: container needs all-interface binding
    fastapi_port: int = 8000
    log_level: str = "INFO"
    precedent_cache_ttl_seconds: int = 86400
    pair_api_url: str = "https://search.pair.gov.sg/api/v1/search"
    # When None, PAIR HTTP calls are skipped; vector store is primary.
    pair_api_key: str | None = None
    pair_circuit_breaker_threshold: int = 3
    pair_circuit_breaker_timeout: int = 60

    # LangGraph checkpointer mode — "postgres" wires the AsyncPostgresSaver via
    # the FastAPI lifespan / arq startup hook; "disabled" skips it (useful for
    # smoke tests or when running against an env without Postgres).
    langgraph_checkpointer: str = "postgres"

    # Environment tag (Sprint 1 1.C3a.1). Injected into every graph run's
    # `metadata={"env": ...}` so LangSmith traces can be filtered by env
    # without splitting the project. Values: "dev" | "staging" | "prod".
    app_env: str = "dev"

    # LangGraph runtime selector (Sprint 1 1.DEP1.3). "in_process" runs
    # the compiled graph in this Python process (current behaviour);
    # "cloud" routes through the LangGraph Cloud HTTP API (wired in
    # Sprint 5 task 5.DEP.6). Default keeps existing local + tests
    # working unchanged.
    graph_runtime: str = "in_process"

    @property
    def pipeline_conversational_streaming_phases(self) -> list[str]:
        """Q1.3 — phases enrolled in conversational streaming
        (`llm_token`, `tool_call_delta`). Read fresh from
        `PIPELINE_CONVERSATIONAL_STREAMING_PHASES` (comma-separated)
        each access so a hot env-var flip doesn't require a process
        restart. Default empty → off everywhere, no behaviour change.
        Q1.4 reads this when building each phase node; Q1.13 expands
        to `intake,triage` after the rollout. The audit phase is
        NEVER enrolled (architecture decision A3 — strict-correctness
        path stays JSON-only).

        Implemented as a property reading `os.environ` directly to
        sidestep pydantic-settings' JSON-decode-first behaviour for
        `list[str]` fields."""
        raw = os.environ.get("PIPELINE_CONVERSATIONAL_STREAMING_PHASES", "")
        return [p.strip() for p in raw.split(",") if p.strip()]

    # OpenAI Models
    openai_vector_store_id: str = ""
    openai_model_lightweight: str = "gpt-5.4-nano"
    openai_model_efficient_reasoning: str = "gpt-5-mini"
    openai_model_strong_reasoning: str = "gpt-5"
    openai_model_frontier_reasoning: str = "gpt-5.4"
    # Intake extractor — defaults to the lightweight tier so it runs without
    # org verification. Override via env if the org is verified and you want
    # the efficient-reasoning model's better structured-output behaviour.
    openai_model_intake: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
