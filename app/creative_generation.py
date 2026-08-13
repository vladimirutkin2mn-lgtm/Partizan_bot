from __future__ import annotations

import base64
import json
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from openai import OpenAI
from pydantic import BaseModel, Field, HttpUrl

from app.config import get_settings
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
from app.creative_blob_store import CreativeBlobStore, creative_blob_store
from app.distribution_types import DistributionPlatform


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


class OpenAIMetaImageCreativeGenerator:
    """Generate paid Meta images with OpenAI and host them through Partizan's public blob route."""

    def __init__(
        self,
        *,
        api_key: str | None,
        public_base_url: str | None,
        model: str = "gpt-image-2",
        quality: str = "medium",
        client=None,
        blob_store: CreativeBlobStore | None = None,
    ) -> None:
        self._api_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        self._public_base_url = public_base_url.rstrip("/") if public_base_url else None
        self._model = model.strip() or "gpt-image-2"
        self._quality = quality.strip().lower() or "medium"
        self._client = client
        self._blob_store = blob_store or creative_blob_store

    def generate(self, brief: CreativeBriefView) -> CreativeGeneratorResult:
        if brief.platform != DistributionPlatform.INSTAGRAM or brief.media_type != CreativeMediaType.IMAGE:
            return CreativeGeneratorResult(
                outcome=CreativeGenerationOutcome.UNAVAILABLE,
                message=(
                    "The OpenAI image provider currently supports autonomous Instagram/Meta "
                    "paid-image creatives only."
                ),
                provenance={"generator": "openai", "model": self._model},
            )
        if self._api_key is None:
            return CreativeGeneratorResult(
                outcome=CreativeGenerationOutcome.UNAVAILABLE,
                message="OPENAI_API_KEY is required for OpenAI creative generation.",
                provenance={"generator": "openai", "model": self._model},
            )
        if self._public_base_url is None:
            return CreativeGeneratorResult(
                outcome=CreativeGenerationOutcome.UNAVAILABLE,
                message=(
                    "PARTIZAN_PUBLIC_BASE_URL is required so Meta can fetch the generated image."
                ),
                provenance={"generator": "openai", "model": self._model},
            )

        client = self._client or OpenAI(api_key=self._api_key)
        try:
            response = client.images.generate(
                model=self._model,
                prompt=self._prompt(brief),
                size="1024x1536",
                quality=self._quality,
                output_format="png",
            )
            rows = getattr(response, "data", None)
            encoded = getattr(rows[0], "b64_json", None) if rows else None
            if not isinstance(encoded, str) or not encoded:
                return CreativeGeneratorResult(
                    outcome=CreativeGenerationOutcome.FAILED,
                    message="OpenAI image generation returned no image data.",
                    provenance={"generator": "openai", "model": self._model},
                )
            image_bytes = base64.b64decode(encoded, validate=True)
            blob = self._blob_store.put(data=image_bytes, mime_type="image/png")
        except Exception:
            return CreativeGeneratorResult(
                outcome=CreativeGenerationOutcome.FAILED,
                message="OpenAI image generation failed without a usable creative asset.",
                provenance={"generator": "openai", "model": self._model},
            )

        return CreativeGeneratorResult(
            outcome=CreativeGenerationOutcome.READY,
            public_url=f"{self._public_base_url}/v1/public/creative-blobs/{blob.id}",
            mime_type="image/png",
            width=1024,
            height=1536,
            provenance={
                "generator": "openai",
                "model": self._model,
                "quality": self._quality,
                "blob_id": str(blob.id),
                "sha256": blob.sha256,
            },
            message="OpenAI generated a provider-ready Meta image creative.",
        )

    def _prompt(self, brief: CreativeBriefView) -> str:
        content = json.dumps(
            brief.content,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        constraints = "\n".join(f"- {item}" for item in brief.constraints)
        return (
            "Create a premium, scroll-stopping paid social advertisement image for Instagram. "
            "The image must work as a 2:3 portrait creative, feel native to a modern social feed, "
            "and communicate the product idea visually without fabricating claims, testimonials, "
            "ratings, press logos, or social proof. Avoid tiny text and avoid placing critical "
            "content near the edges. Prefer a strong visual concept over a text-heavy poster.\n\n"
            f"Confirmed creative brief:\n{content}\n\nConstraints:\n{constraints}"
        )[:12000]


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


def build_creative_generator() -> CreativeGenerator:
    settings = get_settings()
    if settings.creative_provider == "openai":
        return OpenAIMetaImageCreativeGenerator(
            api_key=settings.openai_api_key,
            public_base_url=settings.partizan_public_base_url,
            model=settings.creative_image_model,
            quality=settings.creative_image_quality,
        )
    return UnavailableCreativeGenerator()


creative_generation_service = CreativeGenerationService(build_creative_generator())
