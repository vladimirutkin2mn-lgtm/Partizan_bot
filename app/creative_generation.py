from __future__ import annotations

from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from app.creative_assets import (
    CreativeAssetRegisterRequest,
    CreativeAssetSource,
    CreativeAssetStatus,
    CreativeAssetView,
    CreativeBriefView,
    CreativeMediaType,
    CreativeReadinessStatus,
    CreativeReadinessView,
    creative_asset_service,
)


class CreativeGenerationOutcome(StrEnum):
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class CreativeGeneratorResult(BaseModel):
    outcome: CreativeGenerationOutcome
    public_url: HttpUrl | None = None
    provider_asset_id: str | None = Field(default=None, min_length=1, max_length=300)
    mime_type: str | None = Field(default=None, min_length=1, max_length=120)
    width: int | None = Field(default=None, ge=1, le=20000)
    height: int | None = Field(default=None, ge=1, le=20000)
    duration_seconds: float | None = Field(default=None, gt=0, le=3600)
    provenance: dict = Field(default_factory=dict)
    message: str = Field(min_length=1, max_length=1000)


class CreativeGenerationView(BaseModel):
    action_id: UUID
    outcome: CreativeGenerationOutcome
    brief: CreativeBriefView
    asset: CreativeAssetView | None = None
    readiness: CreativeReadinessView
    message: str


class CreativeGenerator(Protocol):
    def generate(self, brief: CreativeBriefView) -> CreativeGeneratorResult: ...


class UnavailableCreativeGenerator:
    def generate(self, brief: CreativeBriefView) -> CreativeGeneratorResult:
        return CreativeGeneratorResult(
            outcome=CreativeGenerationOutcome.UNAVAILABLE,
            message=(
                f"No creative generator/upload provider is configured for "
                f"{brief.platform.value} {brief.media_type.value}."
            ),
            provenance={"generator": "unavailable"},
        )


class DeterministicMockCreativeGenerator:
    """Deterministic local/test-only generator. Never used by the production default service."""

    def generate(self, brief: CreativeBriefView) -> CreativeGeneratorResult:
        short = brief.fingerprint[:20]
        if brief.media_type == CreativeMediaType.IMAGE:
            return CreativeGeneratorResult(
                outcome=CreativeGenerationOutcome.READY,
                public_url=f"https://creative.mock.invalid/{short}.png",
                mime_type="image/png",
                width=1080,
                height=1350,
                provenance={"generator": "deterministic_mock", "fingerprint": brief.fingerprint},
                message="Deterministic mock image created for local/test execution.",
            )
        provider_asset_id = None
        public_url = f"https://creative.mock.invalid/{short}.mp4"
        if brief.platform.value == "TIKTOK":
            provider_asset_id = f"mock_tiktok_video_{short}"
        return CreativeGeneratorResult(
            outcome=CreativeGenerationOutcome.READY,
            public_url=public_url,
            provider_asset_id=provider_asset_id,
            mime_type="video/mp4",
            width=1080,
            height=1920,
            duration_seconds=12,
            provenance={"generator": "deterministic_mock", "fingerprint": brief.fingerprint},
            message="Deterministic mock video created for local/test execution.",
        )


class CreativeGenerationService:
    def __init__(self, generator: CreativeGenerator | None = None) -> None:
        self._generator = generator or UnavailableCreativeGenerator()

    def ensure_ready(self, action_id: UUID) -> CreativeGenerationView:
        readiness = creative_asset_service.readiness(action_id)
        if readiness.status == CreativeReadinessStatus.READY:
            return CreativeGenerationView(
                action_id=action_id,
                outcome=CreativeGenerationOutcome.READY,
                brief=readiness.brief,
                asset=readiness.selected_asset,
                readiness=readiness,
                message="Existing provider-ready CreativeAsset reused.",
            )

        reusable = self._existing_generated(readiness.brief)
        if reusable is not None:
            refreshed = creative_asset_service.readiness(action_id)
            if refreshed.status == CreativeReadinessStatus.READY:
                return CreativeGenerationView(
                    action_id=action_id,
                    outcome=CreativeGenerationOutcome.READY,
                    brief=refreshed.brief,
                    asset=refreshed.selected_asset,
                    readiness=refreshed,
                    message="Existing generated CreativeAsset reused by brief fingerprint.",
                )

        result = self._generator.generate(readiness.brief)
        if result.outcome == CreativeGenerationOutcome.UNAVAILABLE:
            return CreativeGenerationView(
                action_id=action_id,
                outcome=result.outcome,
                brief=readiness.brief,
                readiness=readiness,
                message=result.message,
            )
        if result.outcome == CreativeGenerationOutcome.FAILED:
            asset = creative_asset_service.register_asset(
                CreativeAssetRegisterRequest(
                    brief_id=readiness.brief.id,
                    source=CreativeAssetSource.GENERATED,
                    status=CreativeAssetStatus.FAILED,
                    provenance=result.provenance,
                    failure_reason=result.message,
                )
            )
            return CreativeGenerationView(
                action_id=action_id,
                outcome=result.outcome,
                brief=readiness.brief,
                asset=asset,
                readiness=readiness,
                message=result.message,
            )

        asset = creative_asset_service.register_asset(
            CreativeAssetRegisterRequest(
                brief_id=readiness.brief.id,
                source=CreativeAssetSource.GENERATED,
                status=CreativeAssetStatus.READY,
                public_url=result.public_url,
                provider_asset_id=result.provider_asset_id,
                mime_type=result.mime_type,
                width=result.width,
                height=result.height,
                duration_seconds=result.duration_seconds,
                provenance=result.provenance,
            )
        )
        refreshed = creative_asset_service.readiness(action_id)
        if refreshed.status != CreativeReadinessStatus.READY:
            return CreativeGenerationView(
                action_id=action_id,
                outcome=CreativeGenerationOutcome.FAILED,
                brief=readiness.brief,
                asset=asset,
                readiness=refreshed,
                message=(
                    "Generator returned an asset, but it is not provider-ready for this action: "
                    + " ".join(refreshed.reasons)
                )[:1000],
            )
        return CreativeGenerationView(
            action_id=action_id,
            outcome=CreativeGenerationOutcome.READY,
            brief=refreshed.brief,
            asset=refreshed.selected_asset,
            readiness=refreshed,
            message=result.message,
        )

    def _existing_generated(self, brief: CreativeBriefView) -> CreativeAssetView | None:
        for asset in creative_asset_service.list_assets(brief.product_id):
            if (
                asset.brief_fingerprint == brief.fingerprint
                and asset.source == CreativeAssetSource.GENERATED
                and asset.status == CreativeAssetStatus.READY
            ):
                return asset
        return None


creative_generation_service = CreativeGenerationService()
