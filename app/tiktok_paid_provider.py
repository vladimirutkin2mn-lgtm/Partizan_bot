from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from app.distribution_types import DistributionPlatform
from app.runtime_store import RuntimeStateStore, get_runtime_store

TIKTOK_PAID_PROVIDER_CONNECTION_NAMESPACE = "tiktok_paid_provider_connection"


class TikTokPaidProviderConnectionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"


class TikTokPaidProviderConnectionCreateRequest(BaseModel):
    advertiser_id: str = Field(min_length=1, max_length=120)
    access_token_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,119}$")
    api_version: str = Field(default="v1.3", pattern=r"^v\d+\.\d+$")
    location_ids: list[str] = Field(min_length=1, max_length=100)
    video_id: str = Field(min_length=1, max_length=200)
    identity_id: str = Field(min_length=1, max_length=200)
    identity_type: str = Field(min_length=1, max_length=100)
    call_to_action: str = Field(default="LEARN_MORE", min_length=1, max_length=100)
    placements: list[str] = Field(default_factory=lambda: ["PLACEMENT_TIKTOK"], min_length=1)
    languages: list[str] = Field(default_factory=list, max_length=50)
    billing_event: str = Field(min_length=1, max_length=100)
    optimization_goal: str = Field(min_length=1, max_length=100)
    pacing: str = Field(min_length=1, max_length=100)
    budget_mode: str = Field(min_length=1, max_length=100)
    schedule_type: str = Field(min_length=1, max_length=100)
    promotion_type: str | None = Field(default=None, max_length=100)
    test_days: int = Field(default=5, ge=1, le=30)
    status: TikTokPaidProviderConnectionStatus = TikTokPaidProviderConnectionStatus.ACTIVE

    @field_validator("advertiser_id", "video_id", "identity_id")
    @classmethod
    def strip_identifiers(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider identifiers cannot be blank")
        return normalized

    @field_validator("location_ids", "placements", "languages")
    @classmethod
    def normalize_list_values(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if item and item not in normalized:
                normalized.append(item)
        return normalized


class TikTokPaidProviderConnectionView(BaseModel):
    id: UUID
    product_id: UUID
    platform: DistributionPlatform = DistributionPlatform.TIKTOK
    provider: str = "TIKTOK_MARKETING_API"
    advertiser_id: str
    access_token_env: str
    api_version: str
    location_ids: list[str]
    video_id: str
    identity_id: str
    identity_type: str
    call_to_action: str
    placements: list[str]
    languages: list[str]
    billing_event: str
    optimization_goal: str
    pacing: str
    budget_mode: str
    schedule_type: str
    promotion_type: str | None = None
    test_days: int
    status: TikTokPaidProviderConnectionStatus


class TikTokPaidProviderConnectionService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def upsert(
        self,
        product_id: UUID,
        payload: TikTokPaidProviderConnectionCreateRequest,
    ) -> TikTokPaidProviderConnectionView:
        existing = self.get(product_id)
        connection = TikTokPaidProviderConnectionView(
            id=existing.id if existing else uuid4(),
            product_id=product_id,
            **payload.model_dump(),
        )
        self._store.put(
            TIKTOK_PAID_PROVIDER_CONNECTION_NAMESPACE,
            str(product_id),
            connection.model_dump(mode="json"),
        )
        return connection

    def get(self, product_id: UUID) -> TikTokPaidProviderConnectionView | None:
        payload = self._store.get(TIKTOK_PAID_PROVIDER_CONNECTION_NAMESPACE, str(product_id))
        if payload is None:
            return None
        return TikTokPaidProviderConnectionView.model_validate(payload)

    def require_active(self, product_id: UUID) -> TikTokPaidProviderConnectionView:
        connection = self.get(product_id)
        if connection is None:
            raise KeyError(product_id)
        if connection.status != TikTokPaidProviderConnectionStatus.ACTIVE:
            raise ValueError("TikTok paid provider connection is not ACTIVE")
        return connection

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(TIKTOK_PAID_PROVIDER_CONNECTION_NAMESPACE)


tiktok_paid_provider_connection_service = TikTokPaidProviderConnectionService()
