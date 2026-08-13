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
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_schemas import DistributionActionView
from app.distribution_types import DistributionActionType, DistributionPlatform
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
from app.tiktok_direct_post import (
    TikTokDirectPostAttemptStatus,
    TikTokDirectPostService,
    tiktok_direct_post_service,
)
from app.tiktok_direct_post_reconciliation import (
    TikTokDirectPostReconciliationService,
    TikTokDirectPostReconciliationStatus,
    TikTokPostStatusApiError,
    tiktok_direct_post_reconciliation_service,
)
from app.tiktok_owned_publishing import (
    TikTokCreatorInfoApiError,
    TikTokCreatorPublishPreflightService,
    tiktok_creator_publish_preflight_service,
)
from app.tiktok_publish_authorization import (
    TikTokPublishAuthorizationService,
    tiktok_publish_authorization_service,
)


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


class TikTokPermissionedOrganicVideoExecutionAdapter(OrganicVideoCreativeExecutionAdapter):
    """Resumes TikTok owned publishing only after creator-controlled authorization exists."""

    name = "tiktok-permissioned-organic-video"
    provider = "tiktok-content-posting-api"

    def __init__(
        self,
        *,
        generation_service: CreativeGenerationService | None = None,
        attribution_store: RuntimeStateStore | None = None,
        preflight_service: TikTokCreatorPublishPreflightService | None = None,
        authorization_service: TikTokPublishAuthorizationService | None = None,
        direct_post_service: TikTokDirectPostService | None = None,
        reconciliation_service: TikTokDirectPostReconciliationService | None = None,
    ) -> None:
        super().__init__(
            generation_service=generation_service,
            attribution_store=attribution_store,
        )
        self._preflight_service = (
            preflight_service or tiktok_creator_publish_preflight_service
        )
        self._authorization_service = (
            authorization_service or tiktok_publish_authorization_service
        )
        self._direct_post_service = direct_post_service or tiktok_direct_post_service
        self._reconciliation_service = (
            reconciliation_service or tiktok_direct_post_reconciliation_service
        )

    def supports(self, action: DistributionActionView) -> bool:
        if (
            action.platform != DistributionPlatform.TIKTOK
            or action.action_type != DistributionActionType.ORGANIC_VIDEO
            or action.distribution_identity_id is None
        ):
            return False
        try:
            identity = distribution_control_plane_service.get_identity(
                action.distribution_identity_id
            )
        except KeyError:
            return False
        provider = str(identity.profile_config.get("execution_provider") or "").strip().lower()
        return provider == "tiktok_content_posting"

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
                    "TikTok owned publishing is blocked until an action-level READY video exists. "
                    f"{generation.message}"
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

        try:
            attempt = self._direct_post_service.get_latest(action.id)
        except KeyError:
            attempt = None

        if attempt is not None and attempt.status in {
            TikTokDirectPostAttemptStatus.SUBMITTED,
            TikTokDirectPostAttemptStatus.RECONCILIATION_REQUIRED,
        }:
            if attempt.provider_publish_id is None:
                return self._record_owned_receipt(
                    action=action,
                    readiness=readiness,
                    outcome=AdapterExecutionOutcome.ASSISTED,
                    message=(
                        "TikTok Direct Post has an ambiguous external result without a confirmed "
                        "publish_id. Operator reconciliation is required; Partizan will not repost."
                    ),
                    requires_operator_confirmation=True,
                    metadata={"direct_post_attempt_id": str(attempt.id)},
                )
            try:
                reconciliation = self._reconciliation_service.reconcile(
                    action.id,
                    mark_executed=False,
                )
            except (TikTokPostStatusApiError, KeyError, RuntimeError, ValueError) as exc:
                return self._record_owned_receipt(
                    action=action,
                    readiness=readiness,
                    outcome=AdapterExecutionOutcome.IN_PROGRESS,
                    message=(
                        "TikTok accepted the publication, but provider status is temporarily "
                        f"unavailable. Read-only reconciliation will retry. {str(exc)[:900]}"
                    )[:2000],
                    requires_operator_confirmation=False,
                    external_reference=attempt.provider_publish_id,
                    metadata={"direct_post_attempt_id": str(attempt.id)},
                )
            if reconciliation.status == TikTokDirectPostReconciliationStatus.PROCESSING:
                return self._record_owned_receipt(
                    action=action,
                    readiness=readiness,
                    outcome=AdapterExecutionOutcome.IN_PROGRESS,
                    message=(
                        "TikTok Direct Post is processing. No creator action is required; Partizan "
                        "will continue read-only status reconciliation."
                    ),
                    requires_operator_confirmation=False,
                    external_reference=attempt.provider_publish_id,
                    metadata={
                        "direct_post_attempt_id": str(attempt.id),
                        "provider_status": reconciliation.provider_status.value,
                    },
                )
            if reconciliation.status == TikTokDirectPostReconciliationStatus.PUBLISHED:
                return self._record_owned_receipt(
                    action=action,
                    readiness=readiness,
                    outcome=AdapterExecutionOutcome.EXECUTED,
                    message="TikTok provider confirmed PUBLISH_COMPLETE for the authorized video.",
                    requires_operator_confirmation=False,
                    external_reference=attempt.provider_publish_id,
                    metadata={
                        "direct_post_attempt_id": str(attempt.id),
                        "provider_status": reconciliation.provider_status.value,
                        "public_post_ids": list(reconciliation.public_post_ids),
                    },
                )
            return self._record_owned_receipt(
                action=action,
                readiness=readiness,
                outcome=AdapterExecutionOutcome.FAILED,
                message=(
                    "TikTok provider confirmed that the publication failed. A fresh creator "
                    "preflight and new explicit authorization are required before another attempt."
                ),
                requires_operator_confirmation=True,
                external_reference=attempt.provider_publish_id,
                metadata={
                    "direct_post_attempt_id": str(attempt.id),
                    "provider_status": reconciliation.provider_status.value,
                    "provider_fail_reason": reconciliation.fail_reason,
                },
            )

        if attempt is not None and attempt.status == TikTokDirectPostAttemptStatus.STARTED:
            resumed = self._direct_post_service.submit(action.id)
            return self._receipt_from_submission(action, readiness, resumed)

        try:
            self._authorization_service.get_current(action.id, require_usable=True)
        except (KeyError, ValueError):
            try:
                preflight = self._preflight_service.get_latest(
                    action.id,
                    require_fresh=True,
                )
            except (KeyError, ValueError):
                try:
                    preflight = self._preflight_service.refresh(action.id)
                except (TikTokCreatorInfoApiError, KeyError, RuntimeError, ValueError) as exc:
                    return self._record_owned_receipt(
                        action=action,
                        readiness=readiness,
                        outcome=AdapterExecutionOutcome.ASSISTED,
                        message=(
                            "Action-level video is READY, but TikTok creator publishing preflight "
                            f"is unavailable. Creator authorization cannot be requested yet. {str(exc)[:800]}"
                        )[:2000],
                        requires_operator_confirmation=True,
                    )
            return self._record_owned_receipt(
                action=action,
                readiness=readiness,
                outcome=AdapterExecutionOutcome.ASSISTED,
                message=(
                    "TikTok video is READY and current creator capabilities are loaded. Explicit "
                    "creator publish authorization is required before Partizan can post it."
                ),
                requires_operator_confirmation=True,
                metadata={
                    "creator_preflight_id": str(preflight.id),
                    "creator_nickname": preflight.creator_nickname,
                    "privacy_level_options": [
                        value.value for value in preflight.privacy_level_options
                    ],
                    "preflight_expires_at": preflight.expires_at.isoformat(),
                },
            )

        try:
            submitted = self._direct_post_service.submit(action.id)
        except (KeyError, RuntimeError, ValueError) as exc:
            return self._record_owned_receipt(
                action=action,
                readiness=readiness,
                outcome=AdapterExecutionOutcome.ASSISTED,
                message=(
                    "A creator authorization exists, but TikTok Direct Post could not start "
                    f"safely. {str(exc)[:900]}"
                )[:2000],
                requires_operator_confirmation=True,
            )
        return self._receipt_from_submission(action, readiness, submitted)

    def _receipt_from_submission(
        self,
        action: DistributionActionView,
        readiness,
        attempt,
    ) -> ExecutionAdapterReceipt:
        metadata = {"direct_post_attempt_id": str(attempt.id)}
        if attempt.provider_publish_id:
            metadata["provider_publish_id"] = attempt.provider_publish_id
        if attempt.status == TikTokDirectPostAttemptStatus.SUBMITTED:
            return self._record_owned_receipt(
                action=action,
                readiness=readiness,
                outcome=AdapterExecutionOutcome.IN_PROGRESS,
                message=(
                    "Creator-authorized TikTok Direct Post was submitted with a real publish_id. "
                    "Partizan will continue read-only provider reconciliation."
                ),
                requires_operator_confirmation=False,
                external_reference=attempt.provider_publish_id,
                metadata=metadata,
            )
        if attempt.status == TikTokDirectPostAttemptStatus.RECONCILIATION_REQUIRED:
            return self._record_owned_receipt(
                action=action,
                readiness=readiness,
                outcome=AdapterExecutionOutcome.ASSISTED,
                message=(
                    "TikTok Direct Post has an ambiguous external result. Partizan will not retry "
                    "the publication blindly; operator reconciliation is required."
                ),
                requires_operator_confirmation=True,
                external_reference=attempt.provider_publish_id,
                metadata=metadata,
            )
        if attempt.status == TikTokDirectPostAttemptStatus.REJECTED:
            return self._record_owned_receipt(
                action=action,
                readiness=readiness,
                outcome=AdapterExecutionOutcome.FAILED,
                message=(
                    "TikTok rejected the authorized publication. A fresh provider preflight and "
                    "new explicit creator authorization are required before another attempt."
                ),
                requires_operator_confirmation=True,
                metadata=metadata,
            )
        return self._record_owned_receipt(
            action=action,
            readiness=readiness,
            outcome=AdapterExecutionOutcome.ASSISTED,
            message=(
                "TikTok publication did not reach a safely resumable submitted state. Creator or "
                "operator review is required before another provider mutation."
            ),
            requires_operator_confirmation=True,
            metadata=metadata,
        )

    def _record_owned_receipt(
        self,
        *,
        action: DistributionActionView,
        readiness,
        outcome: AdapterExecutionOutcome,
        message: str,
        requires_operator_confirmation: bool,
        external_reference: str | None = None,
        metadata: dict | None = None,
    ) -> ExecutionAdapterReceipt:
        receipt = ExecutionAdapterReceipt(
            action_id=action.id,
            adapter_name=self.name,
            provider=self.provider,
            outcome=outcome,
            message=message[:2000],
            requires_operator_confirmation=requires_operator_confirmation,
            external_reference=external_reference,
            metadata=metadata or {},
            created_at=datetime.now(UTC),
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
            TikTokPermissionedOrganicVideoExecutionAdapter(
                generation_service=organic_generation_service,
                attribution_store=runtime_store,
            ),
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
