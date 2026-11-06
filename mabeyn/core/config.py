from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Runtime connection: restricted role, subject to row-level security.
    database_url: str = "postgresql+asyncpg://mabeyn_app:mabeyn_app@localhost:5432/mabeyn"
    # Owner connection: migrations and tenant provisioning only.
    admin_database_url: str = "postgresql+asyncpg://mabeyn:mabeyn@localhost:5432/mabeyn"
    redis_url: str = "redis://localhost:6379/0"
    openai_api_key: SecretStr = SecretStr("")
    log_level: str = "INFO"

    llm_model: str = "gpt-4.1-mini"
    llm_model_small: str = "gpt-4o-mini"
    judge_model: str = "gpt-4.1-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    llm_timeout_seconds: float = 60.0
    llm_ledger_path: str | None = None

    max_upload_bytes: int = 10 * 1024 * 1024
    # Comma separated hosts that tools may call even though they are not public (dev, mock).
    tool_allowed_hosts: str = ""
    tool_timeout_seconds: float = 10.0
    tool_max_response_bytes: int = 65536
    url_fetch_timeout_seconds: float = 15.0

    # Short-term memory: the history sent to the agents never exceeds this many tokens.
    history_token_budget: int = 1500
    history_keep_recent: int = 6
    summary_max_tokens: int = 300

    # Long-term memory.
    memory_ttl_days: int = 180
    memory_max_records: int = 25
    memory_recall_limit: int = 10
    memory_idle_minutes: int = 30

    # Session creation limits per window (seconds).
    session_rate_window_seconds: int = 60
    session_rate_per_key: int = 60
    session_rate_per_address: int = 20

    # Chat requests per window (seconds), per customer, per API key and per tenant.
    chat_rate_window_seconds: int = 60
    chat_rate_per_customer: int = 20
    chat_rate_per_key: int = 300
    chat_rate_per_tenant: int = 600

    # A pending approval nobody decides within this time is closed as expired.
    approval_ttl_hours: int = 72
    # Pending approvals are announced to the tenant's webhook; failed deliveries are retried.
    webhook_timeout_seconds: float = 5.0
    webhook_max_attempts: int = 5

    # Answer cache for knowledge questions.
    cache_enabled: bool = True
    cache_ttl_seconds: int = 86400
    # Candidates must be this similar before a small model confirms the questions are the same.
    cache_semantic_threshold: float = 0.60
    cache_max_entries: int = 500

    # Browsers may call the API from these origins (comma separated, or * for any). Requests carry
    # bearer tokens or API keys, never cookies; publishable keys also check their own origin list.
    cors_allow_origins: str = "*"

    jwt_secret: SecretStr = Field(
        default=SecretStr("dev-only-secret-change-before-deploying"), min_length=32
    )
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    customer_token_ttl_minutes: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()
