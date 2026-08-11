from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    app_log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://partizan:partizan@localhost:5432/partizan"
    runtime_storage: str = "memory"
    llm_provider: str = "mock"
    llm_model: str = "gpt-5.6"
    search_provider: str = "mock"
    search_model: str = "gpt-5.6-terra"
    openai_api_key: str | None = None
    execution_provider: str = "mock"
    operator_auth_required: bool = False
    operator_api_key: SecretStr | None = None
    partizan_public_base_url: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_starttls: bool = True

    @field_validator("operator_api_key", mode="before")
    @classmethod
    def normalize_operator_api_key(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("partizan_public_base_url", mode="before")
    @classmethod
    def normalize_partizan_public_base_url(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip().rstrip("/")
        if not normalized:
            return None
        parts = urlsplit(normalized)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("PARTIZAN_PUBLIC_BASE_URL must be an absolute http(s) origin")
        if parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise ValueError("PARTIZAN_PUBLIC_BASE_URL must not contain a path, query or fragment")
        return normalized

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
