from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Required
    openai_api_key: str = ""
    solace_broker_url: str = "tcp://localhost:55555"
    solace_broker_vpn: str = "verdictcouncil"
    solace_broker_username: str = "vc-agent"
    solace_broker_password: str = "vc-agent-password"
    database_url: str = "postgresql://vc_dev:vc_dev_password@localhost:5432/verdictcouncil"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "change-me-in-production"

    # Application
    namespace: str = "verdictcouncil"
    fastapi_host: str = "0.0.0.0"
    fastapi_port: int = 8000
    log_level: str = "INFO"
    precedent_cache_ttl_seconds: int = 86400
    pair_api_url: str = "https://search.pair.gov.sg/api/v1/search"

    # OpenAI Models
    openai_vector_store_id: str = ""
    openai_model_lightweight: str = "gpt-5.4-nano"
    openai_model_efficient_reasoning: str = "gpt-5-mini"
    openai_model_strong_reasoning: str = "gpt-5"
    openai_model_frontier_reasoning: str = "gpt-5.4"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
