from __future__ import annotations

from contextvars import ContextVar
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel

from app.creative_assets import (
    CreativeAssetService,
    CreativeAssetView,
    CreativeBriefView,
    CreativeReadinessStatus,
    creative_asset_service,
)
from app.distribution_execution_service import distribution_execution_service
from app.distribution_schemas import DistributionActionView
from app.execution_adapters import (
    AssistedCommunityExecutionAdapter,
    DistributionExecutionAdapterService,
    ExecutionAdapterReceipt,
    ExecutionAdapterRegistry,
    MetaAdsExecutionAdapter,
    TelegramBotExecutionAdapter,
    TikTokAdsExecutionAdapter,
    UnavailableOwnedExecutionAdapter,
    UnavailablePaidExecutionAdapter,
)
from app.paid_provider_connections import (
    PaidProviderConnectionService,
    PaidProviderConnectionView,
    paid_provider_connection_service,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.tiktok_paid_provider import (
    TikTokPaidProviderConnectionService,
    TikTokPaidProviderConnectionView,
    tiktok_paid_provider_connection_service,
)

CREATIVE_EXECUTION_ATTRIBUTION_NAMESPACE = "creative_execution_attribution"


class CreativeExecutionSource(StrEnum):
    ACTION_ASSET = "ACTION_ASSET"
    CONNECTION_FALLBACK = "CONNECTION_FALLBACK"


class CreativeExecutionAttributionView(BaseModel):
    id: UUID
    product_id: UUID
    action_id: UUID
    experiment_id: UUID
    platform: str
    brief_id: UUID
    brief_fingerprint: str
    creative_source: CreativeExecutionSource
    asset_id: UUID | None = None
    asset_source: str | None = None
    media_type: str
    adapter_name: str
    adapter_outcome: str
    recorded_at: datetime


_meta_asset_context: ContextVar[CreativeAssetView | None] = ContextVar(
    "partizan_meta_action_creative_asset",
    default=None,
)
_tiktok_asset_context: ContextVar[CreativeAssetView | None] = ContextVar(
    "partizan_tiktok_action_creative_asset",
    default=None,
)


class ActionCreativeMetaConnectionService:
    """Read-only proxy that substitutes the action-level image for one execution context."""

    def __init__(self, base: PaidProviderConnectionService | None = None) -> None:
        self._base = base or paid_provider_connection_service

    def get_meta(self, product_id: UUID) -> PaidProviderConnectionView | None:
        connection = self._base.get_meta(product_id)
        asset = _meta_asset_context.get()
        if connection is None or asset is None or asset.public_url is None:
            return connection
        return connection.model_copy(update={"default_image_url": asset.public_url})


class ActionCreativeTikTokConnectionService:
    """Read-only proxy that substitutes the action-level provider video for one execution context."""

    def __init__(self, base: TikTokPaidProviderConnectionService | None = None) -> None:
        self._base = base or tiktok_paid_provider_connection_service

    def get(self, product_id: UUID) -> TikTokPaidProviderConnectionView | None:
        connection = self._base.get(product_id)
        asset = _tiktok_asset_context.get()
        if connection is None or asset is None or not asset.provider_asset_id:
            return connection
        return connection.model_copy(update={"video_id": asset.provider_asset_id})


class _CreativeAttributionMixin:
    def __init__(
        self,
        *,
        creative_service: CreativeAssetService | None = None,
        attribution_store: RuntimeStateStore | None = None,
    ) -> None:
        self._creative_service = creative_service or creative_asset_service
        self._attribution_store = attribution_store or get_runtime_store()

    def _resolve_creative(
        self,
        action: DistributionActionView,
    ) -> tuple[CreativeBriefView, CreativeAssetView | None, CreativeExecutionSource]:
        readiness = self._creative_service.readiness(action.id)
        if (
            readiness.status == CreativeReadinessStatus.READY
            and readiness.selected_asset is not None
        ):
            return (
                readiness.brief,
                readiness.selected_asset,
                CreativeExecutionSource.ACTION_ASSET,
            )
        return (
            readiness.brief,
            None,
            CreativeExecutionSource.CONNECTION_FALLBACK,
        )

    def _augment_and_record(
        self,
        *,
        action: DistributionActionView,
        receipt: ExecutionAdapterReceipt,
        brief: CreativeBriefView,
        asset: CreativeAssetView | None,
        source: CreativeExecutionSource,
    ) -> ExecutionAdapterReceipt:
        metadata = {
            **receipt.metadata,
            "creative_source": source.value,
            "creative_brief_id": str(brief.id),
            "creative_brief_fingerprint": brief.fingerprint,
            "creative_media_type": brief.media_type.value,
        }
        if asset is not None:
            metadata.update(
                {
                    "creative_asset_id": str(asset.id),
                    "creative_asset_source": asset.source.value,
                }
            )
        updated = receipt.model_copy(update={"metadata": metadata})
        experiment = distribution_execution_service.get_experiment(brief.experiment_id)
        attribution = CreativeExecutionAttributionView(
            id=uuid4(),
            product_id=experiment.product_id,
            action_id=action.id,
            experiment_id=brief.experiment_id,
            platform=action.platform.value,
            brief_id=brief.id,
            brief_fingerprint=brief.fingerprint,
            creative_source=source,
            asset_id=asset.id if asset is not None else None,
            asset_source=asset.source.value if asset is not None else None,
            media_type=brief.media_type.value,
            adapter_name=updated.adapter_name,
            adapter_outcome=updated.outcome.value,
            recorded_at=datetime.now(UTC),
        )
        self._attribution_store.put(
            CREATIVE_EXECUTION_ATTRIBUTION_NAMESPACE,
            str(action.id),
            attribution.model_dump(mode="json"),
        )
        return updated


class MetaCreativeAdsExecutionAdapter(_CreativeAttributionMixin, MetaAdsExecutionAdapter):
    def __init__(
        self,
        *,
        creative_service: CreativeAssetService | None = None,
        attribution_store: RuntimeStateStore | None = None,
        connection_service: PaidProviderConnectionService | None = None,
        **kwargs,
    ) -> None:
        _CreativeAttributionMixin.__init__(
            self,
            creative_service=creative_service,
            attribution_store=attribution_store,
        )
        self._creative_connection_service = ActionCreativeMetaConnectionService(
            connection_service or paid_provider_connection_service
        )
        MetaAdsExecutionAdapter.__init__(
            self,
            connection_service=self._creative_connection_service,
            **kwargs,
        )

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt:
        brief, asset, source = self._resolve_creative(action)
        token = _meta_asset_context.set(asset)
        try:
            receipt = super().execute(action)
        finally:
            _meta_asset_context.reset(token)
        return self._augment_and_record(
            action=action,
            receipt=receipt,
            brief=brief,
            asset=asset,
            source=source,
        )


class TikTokCreativeAdsExecutionAdapter(
    _CreativeAttributionMixin,
    TikTokAdsExecutionAdapter,
):
    def __init__(
        self,
        *,
        creative_service: CreativeAssetService | None = None,
        attribution_store: RuntimeStateStore | None = None,
        connection_service: TikTokPaidProviderConnectionService | None = None,
        **kwargs,
    ) -> None:
        _CreativeAttributionMixin.__init__(
            self,
            creative_service=creative_service,
            attribution_store=attribution_store,
        )
        self._creative_connection_service = ActionCreativeTikTokConnectionService(
            connection_service or tiktok_paid_provider_connection_service
        )
        TikTokAdsExecutionAdapter.__init__(
            self,
            connection_service=self._creative_connection_service,
            **kwargs,
        )

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt:
        brief, asset, source = self._resolve_creative(action)
        token = _tiktok_asset_context.set(asset)
        try:
            receipt = super().execute(action)
        finally:
            _tiktok_asset_context.reset(token)
        return self._augment_and_record(
            action=action,
            receipt=receipt,
            brief=brief,
            asset=asset,
            source=source,
        )


def build_creative_execution_adapter_service(
    *,
    store: RuntimeStateStore | None = None,
) -> DistributionExecutionAdapterService:
    runtime_store = store or get_runtime_store()
    registry = ExecutionAdapterRegistry(
        [
            TelegramBotExecutionAdapter(),
            MetaCreativeAdsExecutionAdapter(attribution_store=runtime_store),
            TikTokCreativeAdsExecutionAdapter(attribution_store=runtime_store),
            AssistedCommunityExecutionAdapter(),
            UnavailableOwnedExecutionAdapter(),
            UnavailablePaidExecutionAdapter(),
        ]
    )
    return DistributionExecutionAdapterService(registry=registry, store=runtime_store)


creative_distribution_execution_adapter_service = build_creative_execution_adapter_service()
