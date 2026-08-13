from __future__ import annotations

import os
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, Field

from app.creative_assets import (
    CreativeAssetRegisterRequest,
    CreativeAssetService,
    CreativeAssetSource,
    CreativeAssetStatus,
    CreativeAssetView,
    CreativeMediaType,
    CreativeReadinessStatus,
    CreativeReadinessView,
    creative_asset_service,
)
from app.creative_generation import (
    CreativeGenerationOutcome,
    CreativeGenerationService,
    CreativeGenerationView,
    creative_generation_service,
)
from app.distribution_types import DistributionPlatform
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.tiktok_creative_api import (
    HttpxTikTokCreativeApiClient,
    TikTokCreativeApiClient,
    TikTokCreativeApiError,
)
from app.tiktok_paid_provider import (
    TikTokPaidProviderConnectionService,
    TikTokPaidProviderConnectionStatus,
    tiktok_paid_provider_connection_service,
)

TIKTOK_VIDEO_UPLOAD_ATTEMPT_NAMESPACE = "tiktok_video_upload_attempt"


class CreativeProviderFinalizationOutcome(StrEnum):
    READY = "READY"
    NOOP = "NOOP"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class TikTokVideoUploadAttemptStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class TikTokVideoUploadAttempt(BaseModel):
    action_id: UUID
    source_asset_id: UUID
    advertiser_id: str = Field(min_length=1, max_length=120)
    file_name: str = Field(min_length=1, max_length=100)
    status: TikTokVideoUploadAttemptStatus
    provider_asset_id: str | None = Field(default=None, min_length=1, max_length=300)
    created_at: datetime
    updated_at: datetime


class CreativeProviderFinalizationView(BaseModel):
    action_id: UUID
    outcome: CreativeProviderFinalizationOutcome
    readiness: CreativeReadinessView
    asset: CreativeAssetView | None = None
    message: str = Field(min_length=1, max_length=1000)


class SecretResolver(Protocol):
    def resolve(self, name: str) -> str | None: ...


class EnvironmentSecretResolver:
    def resolve(self, name: str) -> str | None:
        value = os.getenv(name)
        return value if value and value.strip() else None


class TikTokVideoCreativeFinalizer:
    def __init__(
        self,
        *,
        client: TikTokCreativeApiClient | None = None,
        connection_service: TikTokPaidProviderConnectionService | None = None,
        secret_resolver: SecretResolver | None = None,
        asset_service: CreativeAssetService | None = None,
        store: RuntimeStateStore | None = None,
    ) -> None:
        self._client = client or HttpxTikTokCreativeApiClient()
        self._connection_service = (
            connection_service or tiktok_paid_provider_connection_service
        )
        self._secret_resolver = secret_resolver or EnvironmentSecretResolver()
        self._asset_service = asset_service or creative_asset_service
        self._store = store or get_runtime_store()

    def finalize(self, action_id: UUID) -> CreativeProviderFinalizationView:
        readiness = self._asset_service.readiness(action_id)
        brief = readiness.brief
        if readiness.status == CreativeReadinessStatus.READY:
            return CreativeProviderFinalizationView(
                action_id=action_id,
                outcome=CreativeProviderFinalizationOutcome.READY,
                readiness=readiness,
                asset=readiness.selected_asset,
                message="Creative is already provider-ready; TikTok upload was not repeated.",
            )
        if (
            brief.platform != DistributionPlatform.TIKTOK
            or brief.media_type != CreativeMediaType.VIDEO
        ):
            return CreativeProviderFinalizationView(
                action_id=action_id,
                outcome=CreativeProviderFinalizationOutcome.NOOP,
                readiness=readiness,
                message="TikTok video provider finalization does not apply to this creative brief.",
            )

        connection = self._connection_service.get(brief.product_id)
        if connection is None:
            return self._unavailable(
                action_id,
                readiness,
                "No TikTok paid provider connection is configured for video upload.",
            )
        if connection.status != TikTokPaidProviderConnectionStatus.ACTIVE:
            return self._unavailable(
                action_id,
                readiness,
                "TikTok paid provider connection is not ACTIVE for video upload.",
            )
        access_token = self._secret_resolver.resolve(connection.access_token_env)
        if access_token is None:
            return self._unavailable(
                action_id,
                readiness,
                "TikTok access-token secret is not available for video upload.",
            )

        candidate = self._url_candidate(readiness)
        if candidate is None:
            return CreativeProviderFinalizationView(
                action_id=action_id,
                outcome=CreativeProviderFinalizationOutcome.NOOP,
                readiness=readiness,
                message=(
                    "No HTTPS URL-addressable READY video exists for this brief yet; "
                    "a video source provider or operator upload is still required."
                ),
            )

        key = self._attempt_key(connection.advertiser_id, candidate.id)
        attempt = self._get_attempt(key)
        if attempt is not None:
            if attempt.status == TikTokVideoUploadAttemptStatus.SUCCEEDED:
                assert attempt.provider_asset_id is not None
                return self._promote(
                    readiness=readiness,
                    source=candidate,
                    provider_asset_id=attempt.provider_asset_id,
                    file_name=attempt.file_name,
                )
            return CreativeProviderFinalizationView(
                action_id=action_id,
                outcome=CreativeProviderFinalizationOutcome.RECONCILIATION_REQUIRED,
                readiness=readiness,
                asset=candidate,
                message=(
                    "A previous TikTok video upload has an ambiguous external result. "
                    "Partizan will not retry it blindly; reconcile the provider asset first."
                ),
            )

        file_name = self._file_name(readiness, candidate)
        now = datetime.now(UTC)
        attempt = TikTokVideoUploadAttempt(
            action_id=action_id,
            source_asset_id=candidate.id,
            advertiser_id=connection.advertiser_id,
            file_name=file_name,
            status=TikTokVideoUploadAttemptStatus.STARTED,
            created_at=now,
            updated_at=now,
        )
        self._persist_attempt(key, attempt)
        try:
            provider_asset_id = self._client.upload_video_by_url(
                connection=connection,
                access_token=access_token,
                video_url=str(candidate.public_url),
                file_name=file_name,
            )
        except (TikTokCreativeApiError, RuntimeError, ValueError):
            ambiguous = attempt.model_copy(
                update={
                    "status": TikTokVideoUploadAttemptStatus.RECONCILIATION_REQUIRED,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._persist_attempt(key, ambiguous)
            return CreativeProviderFinalizationView(
                action_id=action_id,
                outcome=CreativeProviderFinalizationOutcome.RECONCILIATION_REQUIRED,
                readiness=readiness,
                asset=candidate,
                message=(
                    "TikTok video upload did not return a confirmed provider result. "
                    "No provider video ID was fabricated and automatic retry is blocked."
                ),
            )

        succeeded = attempt.model_copy(
            update={
                "status": TikTokVideoUploadAttemptStatus.SUCCEEDED,
                "provider_asset_id": provider_asset_id,
                "updated_at": datetime.now(UTC),
            }
        )
        self._persist_attempt(key, succeeded)
        return self._promote(
            readiness=readiness,
            source=candidate,
            provider_asset_id=provider_asset_id,
            file_name=file_name,
        )

    def _promote(
        self,
        *,
        readiness: CreativeReadinessView,
        source: CreativeAssetView,
        provider_asset_id: str,
        file_name: str,
    ) -> CreativeProviderFinalizationView:
        existing = self._asset_service.readiness(readiness.action_id)
        if existing.status == CreativeReadinessStatus.READY:
            return CreativeProviderFinalizationView(
                action_id=readiness.action_id,
                outcome=CreativeProviderFinalizationOutcome.READY,
                readiness=existing,
                asset=existing.selected_asset,
                message="Existing provider-ready TikTok video asset reused.",
            )
        if source.status != CreativeAssetStatus.READY:
            return CreativeProviderFinalizationView(
                action_id=readiness.action_id,
                outcome=CreativeProviderFinalizationOutcome.RECONCILIATION_REQUIRED,
                readiness=existing,
                asset=source,
                message=(
                    "TikTok returned a video ID, but the source video was retired or changed before "
                    "provider attribution could be persisted. Operator reconciliation is required."
                ),
            )

        asset = self._asset_service.register_asset(
            CreativeAssetRegisterRequest(
                brief_id=readiness.brief.id,
                source=CreativeAssetSource.EXISTING_PROVIDER,
                status=CreativeAssetStatus.READY,
                public_url=source.public_url,
                provider_asset_id=provider_asset_id,
                mime_type=source.mime_type,
                width=source.width,
                height=source.height,
                duration_seconds=source.duration_seconds,
                provenance={
                    "provider": "tiktok_marketing_api",
                    "upload_type": "UPLOAD_BY_URL",
                    "source_asset_id": str(source.id),
                    "file_name": file_name,
                },
            )
        )
        refreshed = self._asset_service.readiness(readiness.action_id)
        if refreshed.status != CreativeReadinessStatus.READY:
            return CreativeProviderFinalizationView(
                action_id=readiness.action_id,
                outcome=CreativeProviderFinalizationOutcome.RECONCILIATION_REQUIRED,
                readiness=refreshed,
                asset=asset,
                message=(
                    "TikTok returned a video ID, but provider readiness still did not pass. "
                    "Operator reconciliation is required."
                ),
            )
        return CreativeProviderFinalizationView(
            action_id=readiness.action_id,
            outcome=CreativeProviderFinalizationOutcome.READY,
            readiness=refreshed,
            asset=refreshed.selected_asset,
            message="Video URL uploaded to TikTok Asset Library and provider video ID persisted.",
        )

    def _url_candidate(self, readiness: CreativeReadinessView) -> CreativeAssetView | None:
        for asset in self._asset_service.list_assets(readiness.brief.product_id):
            if (
                asset.brief_fingerprint == readiness.brief.fingerprint
                and asset.platform == DistributionPlatform.TIKTOK
                and asset.media_type == CreativeMediaType.VIDEO
                and asset.status == CreativeAssetStatus.READY
                and asset.public_url is not None
                and not asset.provider_asset_id
                and urlsplit(str(asset.public_url)).scheme == "https"
            ):
                return asset
        return None

    def _file_name(
        self,
        readiness: CreativeReadinessView,
        asset: CreativeAssetView,
    ) -> str:
        return (
            f"partizan-{readiness.brief.fingerprint[:20]}-{asset.id.hex[:12]}.mp4"
        )[:100]

    def _attempt_key(self, advertiser_id: str, asset_id: UUID) -> str:
        return f"{advertiser_id}:{asset_id}"

    def _get_attempt(self, key: str) -> TikTokVideoUploadAttempt | None:
        payload = self._store.get(TIKTOK_VIDEO_UPLOAD_ATTEMPT_NAMESPACE, key)
        if payload is None:
            return None
        return TikTokVideoUploadAttempt.model_validate(payload)

    def _persist_attempt(self, key: str, attempt: TikTokVideoUploadAttempt) -> None:
        self._store.put(
            TIKTOK_VIDEO_UPLOAD_ATTEMPT_NAMESPACE,
            key,
            attempt.model_dump(mode="json"),
        )

    def _unavailable(
        self,
        action_id: UUID,
        readiness: CreativeReadinessView,
        message: str,
    ) -> CreativeProviderFinalizationView:
        return CreativeProviderFinalizationView(
            action_id=action_id,
            outcome=CreativeProviderFinalizationOutcome.UNAVAILABLE,
            readiness=readiness,
            message=message,
        )


class ProviderAwareCreativeGenerationService:
    def __init__(
        self,
        *,
        generation_service: CreativeGenerationService | None = None,
        tiktok_finalizer: TikTokVideoCreativeFinalizer | None = None,
    ) -> None:
        self._generation_service = generation_service or creative_generation_service
        self._tiktok_finalizer = tiktok_finalizer or TikTokVideoCreativeFinalizer()

    def ensure_ready(self, action_id: UUID) -> CreativeGenerationView:
        before = self._tiktok_finalizer.finalize(action_id)
        if before.outcome == CreativeProviderFinalizationOutcome.READY:
            return self._ready_from_finalization(before)
        if before.outcome in {
            CreativeProviderFinalizationOutcome.UNAVAILABLE,
            CreativeProviderFinalizationOutcome.RECONCILIATION_REQUIRED,
        }:
            return CreativeGenerationView(
                action_id=action_id,
                outcome=CreativeGenerationOutcome.UNAVAILABLE,
                brief=before.readiness.brief,
                asset=before.asset,
                readiness=before.readiness,
                message=before.message,
            )
        if before.outcome == CreativeProviderFinalizationOutcome.FAILED:
            return CreativeGenerationView(
                action_id=action_id,
                outcome=CreativeGenerationOutcome.FAILED,
                brief=before.readiness.brief,
                asset=before.asset,
                readiness=before.readiness,
                message=before.message,
            )

        generated = self._generation_service.ensure_ready(action_id)
        if generated.outcome == CreativeGenerationOutcome.READY:
            return generated

        after = self._tiktok_finalizer.finalize(action_id)
        if after.outcome == CreativeProviderFinalizationOutcome.READY:
            return self._ready_from_finalization(after)
        if after.outcome in {
            CreativeProviderFinalizationOutcome.UNAVAILABLE,
            CreativeProviderFinalizationOutcome.RECONCILIATION_REQUIRED,
        }:
            return CreativeGenerationView(
                action_id=action_id,
                outcome=CreativeGenerationOutcome.UNAVAILABLE,
                brief=after.readiness.brief,
                asset=after.asset,
                readiness=after.readiness,
                message=after.message,
            )
        return generated

    def _ready_from_finalization(
        self,
        result: CreativeProviderFinalizationView,
    ) -> CreativeGenerationView:
        return CreativeGenerationView(
            action_id=result.action_id,
            outcome=CreativeGenerationOutcome.READY,
            brief=result.readiness.brief,
            asset=result.asset or result.readiness.selected_asset,
            readiness=result.readiness,
            message=result.message,
        )


def _build_provider_aware_creative_generation_service() -> ProviderAwareCreativeGenerationService:
    from app.gemini_video_generation import build_multimedia_creative_generator

    return ProviderAwareCreativeGenerationService(
        generation_service=CreativeGenerationService(
            generator=build_multimedia_creative_generator()
        )
    )


provider_aware_creative_generation_service = _build_provider_aware_creative_generation_service()
