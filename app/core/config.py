from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "StoreFlow"
    environment: str = "development"
    mysql_url: str = "mysql+pymysql://supplymind:change-me@localhost:3306/supplymind"
    langgraph_checkpoint_url: str = ""
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    frontend_origins: str = "http://localhost:5173"
    api_key: str = ""
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    log_level: str = "INFO"
    rate_limit_per_minute: int = 120
    # BaiLian uses an OpenAI-compatible endpoint. These values are read only
    # from the API process environment and are never sent to the browser.
    bailian_api_key: str = Field(default="", validation_alias=AliasChoices("BAILIAN_API_KEY", "DASHSCOPE_API_KEY"))
    bailian_base_url: str = Field(default="", validation_alias=AliasChoices("BAILIAN_BASE_URL", "DASHSCOPE_BASE_URL"))
    bailian_model: str = "qwen3.7-plus"
    bailian_timeout_seconds: int = 25
    # Optional local accounting rates. They default to zero because pricing
    # varies by model/region; a zero must be shown as "rate not configured",
    # never presented as a free remote call.
    bailian_cost_per_1k_tokens_usd: float = 0.0
    # Keep the interactive Agent decision live by default, but make optional
    # plan/report prose enrichment opt-in so a slow upstream cannot hold a
    # procurement draft hostage during a demo.
    model_enrichment_enabled: bool = False
    # Optional Tavily news retrieval; an empty key leaves the existing public
    # search path untouched.
    tavily_api_key: str = ""
    tavily_max_results: int = 3
    tavily_time_range: str = "week"
    tavily_cost_per_request_usd: float = 0.0
    workspace_id: str = "demo"
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = 1024
    rerank_model: str = "qwen3-rerank"
    bailian_rerank_base_url: str = ""
    rerank_provider: str = "local"
    rag_candidate_limit: int = 30
    rag_final_limit: int = 8
    context_token_budget: int = 12000
    memory_default_ttl_days: int = 90
    # Long-term memory is a historical prior, not primary evidence.  Keep a
    # separate, much smaller budget so it cannot crowd out the Evidence Pack.
    memory_catalog_limit: int = 5
    memory_context_token_budget: int = 1600
    jwt_secret: str = ""
    jwt_issuer: str = "supplymind"
    jwt_audience: str = "supplymind-web"
    jwt_access_ttl_minutes: int = 60
    # Demo-only account source. Use an identity provider or a secret manager
    # before any real deployment; this project does not claim production SSO.
    jwt_users_json: str = ""
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Docker Compose injects local configuration as environment variables.
    # The application deliberately does not open a local .env file.
    model_config = SettingsConfigDict(extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]

    @property
    def allowed_hosts(self) -> list[str]:
        return [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]

    @property
    def model_configuration_error(self) -> str | None:
        if bool(self.bailian_api_key) != bool(self.bailian_base_url):
            return "BAILIAN_API_KEY and BAILIAN_BASE_URL must be configured together"
        return None

    @property
    def model_enabled(self) -> bool:
        return bool(self.bailian_api_key and self.bailian_base_url and not self.model_configuration_error)

    @field_validator("environment")
    @classmethod
    def supported_environment(cls, value: str) -> str:
        if value not in {"development", "test", "production"}:
            raise ValueError("environment must be development, test, or production")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
