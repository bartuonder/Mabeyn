from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://mabeyn:mabeyn@localhost:5432/mabeyn"
    redis_url: str = "redis://localhost:6379/0"
    openai_api_key: SecretStr = SecretStr("")
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
