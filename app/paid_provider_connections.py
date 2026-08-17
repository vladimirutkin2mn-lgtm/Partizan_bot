from __future__ import annotations

import os
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from app.distribution_types import DistributionPlatform
from app.provider_secret_store import PROVIDER_SECRET_PREFIX, provider_secret_store
from app.runtime_store import RuntimeStateStore, get_runtime_store

PAID_PROVIDER_CONNECTION_NAMESPACE = "paid_provider_connection"


class PaidProviderType(StrEnum):
    META_MARKETING_API = "META_MARKETING_API"


class PaidProviderConnectionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"


class PaidProviderConnectionCreateRequest(BaseModel):
    ad_account_id: str = Field(min_length=1, max_length=120)
    page_id: str = Field(min_length=1, max_length=120)
    instagram_actor_id: str | None = Field(default=None, max_length=120)
    access_token_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,119}$")
    api_version: str = Field(pattern=r"^v\d+\.\d+$")
    country_codes: list[str] = Field(min_length=1, max_length=50)
    default_image_url: HttpUrl | None = None
    budget_minor_unit_factor: int = Field(default=100, ge=1, le=1000)
    test_days: int = Field(default=5, ge=1, le=30)
    special_ad_categories: list[str] = Field(default_factory=list, max_length=20)
    status: PaidProviderConnectionStatus = PaidProviderConnectionStatus.ACTIVE

    @field_validator("ad_account_id")
    @classmethod
    def normalize_ad_account_id(cls, value: str) -> str:
        normalized = value.strip()
        if normalized.startswith("act_"):
            normalized = normalized[4:]
        if not normalized:
            raise ValueError("ad_account_id cannot be empty")
        return normalized

    @field_validator("country_codes")
    @classmethod
    def normalize_country_codes(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            code = value.strip().upper()
            if len(code) != 2 or not code.isalpha():
                raise ValueError("country_codes must contain ISO-like two-letter codes")
            if code not in normalized:
                normalized.append(code)
        return normalized

    @model_validator(mode="after")
    def validate_meta_connection(self) -> PaidProviderConnectionCreateRequest:
        if not self.page_id.strip():
            raise ValueError("page_id cannot be blank")
        return self


class PaidProviderConnectionView(BaseModel):
    id: UUID
    product_id: UUID
    platform: DistributionPlatform
    provider: PaidProviderType
    ad_account_id: str
    page_id: str
    instagram_actor_id: str | None = None
    access_token_env: str
    api_version: str
    country_codes: list[str]
    default_image_url: HttpUrl | None = None
    budget_minor_unit_factor: int
    test_days: int
    special_ad_categories: list[str] = Field(default_factory=list)
    status: PaidProviderConnectionStatus


class PaidProviderConnectionService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def upsert_meta(
        self,
        product_id: UUID,
        payload: PaidProviderConnectionCreateRequest,
    ) -> PaidProviderConnectionView:
        existing = self.get_meta(product_id)
        connection = PaidProviderConnectionView(
            id=existing.id if existing is not None else uuid4(),
            product_id=product_id,
            platform=DistributionPlatform.INSTAGRAM,
            provider=PaidProviderType.META_MARKETING_API,
            **payload.model_dump(),
        )
        self._store.put(
            PAID_PROVIDER_CONNECTION_NAMESPACE,
            self._key(product_id),
            connection.model_dump(mode="json"),
        )
        self._hydrate_customer_secret(connection)
        return connection

    def get_meta(self, product_id: UUID) -> PaidProviderConnectionView | None:
        payload = self._store.get(
            PAID_PROVIDER_CONNECTION_NAMESPACE,
            self._key(product_id),
        )
        if payload is None:
            return None
        connection = PaidProviderConnectionView.model_validate(payload)
        self._hydrate_customer_secret(connection)
        return connection

    def require_active_meta(self, product_id: UUID) -> PaidProviderConnectionView:
        connection = self.get_meta(product_id)
        if connection is None:
            raise KeyError(product_id)
        if connection.status != PaidProviderConnectionStatus.ACTIVE:
            raise ValueError("Meta paid provider connection is not ACTIVE")
        return connection

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(PAID_PROVIDER_CONNECTION_NAMESPACE)

    @staticmethod
    def _hydrate_customer_secret(connection: PaidProviderConnectionView) -> None:
        reference = connection.access_token_env
        if not reference.startswith(PROVIDER_SECRET_PREFIX):
            return
        token = provider_secret_store.get(reference)
        if token is not None:
            os.environ[reference] = token

    def _key(self, product_id: UUID) -> str:
        return f"{product_id}:INSTAGRAM:META_MARKETING_API"


paid_provider_connection_service = PaidProviderConnectionService()
