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

    jwt_secret: SecretStr = Field(
        default=SecretStr("dev-only-secret-change-before-deploying"), min_length=32
    )
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    customer_token_ttl_minutes: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()
