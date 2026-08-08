from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    app_log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://partizan:partizan@localhost:5432/partizan"
    llm_provider: str = "mock"
    llm_model: str = "gpt-5.6"
    search_provider: str = "mock"
    search_model: str = "gpt-5.6-terra"
    openai_api_key: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
