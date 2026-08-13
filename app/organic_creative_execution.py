from __future__ import annotations

from datetime import UTC, datetime

from app.creative_assets import CreativeReadinessStatus
from app.creative_execution_adapters import (
    CreativeExecutionSource,
    MetaCreativeAdsExecutionAdapter,
    TikTokCreativeAdsExecutionAdapter,
    _CreativeAttributionMixin,
)
from app.creative_generation import (
    CreativeGenerationOutcome,
    CreativeGenerationService,
)
from app.distribution_schemas import DistributionActionView
from app.distribution_types import DistributionActionType
from app.execution_adapters import (
    AdapterExecutionOutcome,
    AssistedCommunityExecutionAdapter,
    DistributionExecutionAdapterService,
    ExecutionAdapterReceipt,
    ExecutionAdapterRegistry,
    TelegramBotExecutionAdapter,
    UnavailableOwnedExecutionAdapter,
    UnavailablePaidExecutionAdapter,
)
from app.gemini_video_generation import build_multimedia_creative_generator
from app.runtime_store import RuntimeStateStore, get_runtime_store


class OrganicVideoCreativeExecutionAdapter(
    _CreativeAttributionMixin,
    UnavailableOwnedExecutionAdapter,
):
    """Requires a real action-level video before any owned organic execution path may run."""

    name = "owned-organic-video-creative-gate"
    provider = "creative-gate"

    def __init__(
        self,
        *,
        generation_service: CreativeGenerationService | None = None,
        attribution_store: RuntimeStateStore | None = None,
    ) -> None:
        _CreativeAttributionMixin.__init__(
            self,
            attribution_store=attribution_store,
        )
        self._generation_service = generation_service or CreativeGenerationService(
            generator=build_multimedia_creative_generator()
        )

    def supports(self, action: DistributionActionView) -> bool:
        return action.action_type == DistributionActionType.ORGANIC_VIDEO

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt:
        generation = self._generation_service.ensure_ready(action.id)
        readiness = generation.readiness
        if (
            generation.outcome != CreativeGenerationOutcome.READY
            or readiness.status != CreativeReadinessStatus.READY
            or readiness.selected_asset is None
        ):
            outcome = (
                AdapterExecutionOutcome.FAILED
                if generation.outcome == CreativeGenerationOutcome.FAILED
                else AdapterExecutionOutcome.UNAVAILABLE
            )
            return ExecutionAdapterReceipt(
                action_id=action.id,
                adapter_name=self.name,
                provider=self.provider,
                outcome=outcome,
                message=(
                    "Owned organic video execution is blocked until an action-level READY video "
                    f"exists. {generation.message}"
                )[:2000],
                requires_operator_confirmation=True,
                metadata={
                    "creative_brief_id": str(readiness.brief.id),
                    "creative_brief_fingerprint": readiness.brief.fingerprint,
                    "creative_media_type": readiness.brief.media_type.value,
                    "creative_readiness": readiness.status.value,
                    "creative_blockers": list(readiness.reasons),
                },
                created_at=datetime.now(UTC),
            )

        base_receipt = UnavailableOwnedExecutionAdapter.execute(self, action)
        receipt = base_receipt.model_copy(
            update={
                "adapter_name": self.name,
                "provider": self.provider,
                "message": (
                    "Action-level organic video is READY. Public publishing is still unavailable "
                    "until a permissioned, explicit-consent owned-content provider is configured."
                ),
                "requires_operator_confirmation": True,
            }
        )
        return self._augment_and_record(
            action=action,
            receipt=receipt,
            brief=readiness.brief,
            asset=readiness.selected_asset,
            source=CreativeExecutionSource.ACTION_ASSET,
        )


def build_organic_creative_execution_adapter_service(
    *,
    store: RuntimeStateStore | None = None,
    organic_generation_service: CreativeGenerationService | None = None,
) -> DistributionExecutionAdapterService:
    runtime_store = store or get_runtime_store()
    registry = ExecutionAdapterRegistry(
        [
            TelegramBotExecutionAdapter(),
            MetaCreativeAdsExecutionAdapter(attribution_store=runtime_store),
            TikTokCreativeAdsExecutionAdapter(attribution_store=runtime_store),
            AssistedCommunityExecutionAdapter(),
            OrganicVideoCreativeExecutionAdapter(
                generation_service=organic_generation_service,
                attribution_store=runtime_store,
            ),
            UnavailablePaidExecutionAdapter(),
        ]
    )
    return DistributionExecutionAdapterService(registry=registry, store=runtime_store)


organic_creative_distribution_execution_adapter_service = (
    build_organic_creative_execution_adapter_service()
)
