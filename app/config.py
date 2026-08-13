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
    creative_provider: str = "unavailable"
    creative_image_model: str = "gpt-image-2"
    creative_image_quality: str = "medium"
    creative_video_model: str = "sora-2"
    creative_video_seconds: int = 8
    creative_video_size: str = "720x1280"
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

    @field_validator("creative_provider", mode="before")
    @classmethod
    def normalize_creative_provider(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if normalized not in {"unavailable", "openai"}:
            raise ValueError("CREATIVE_PROVIDER must be 'unavailable' or 'openai'")
        return normalized

    @field_validator("creative_image_quality", mode="before")
    @classmethod
    def normalize_creative_image_quality(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if normalized not in {"low", "medium", "high"}:
            raise ValueError("CREATIVE_IMAGE_QUALITY must be low, medium or high")
        return normalized

    @field_validator("creative_video_model", mode="before")
    @classmethod
    def normalize_creative_video_model(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if normalized not in {"sora-2", "sora-2-pro"}:
            raise ValueError("CREATIVE_VIDEO_MODEL must be sora-2 or sora-2-pro")
        return normalized

    @field_validator("creative_video_seconds", mode="before")
    @classmethod
    def normalize_creative_video_seconds(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if value not in {4, 8, 12}:
            raise ValueError("CREATIVE_VIDEO_SECONDS must be 4, 8 or 12")
        return value

    @field_validator("creative_video_size", mode="before")
    @classmethod
    def normalize_creative_video_size(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if normalized not in {"720x1280", "1024x1792"}:
            raise ValueError("CREATIVE_VIDEO_SIZE must be a supported portrait Sora size")
        return normalized

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
