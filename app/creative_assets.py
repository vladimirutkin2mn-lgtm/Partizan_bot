from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, HttpUrl, model_validator

from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.paid_campaign import paid_campaign_spec_service
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

CREATIVE_BRIEF_NAMESPACE = "creative_brief"
CREATIVE_BRIEF_ACTION_NAMESPACE = "creative_brief_action"
CREATIVE_ASSET_NAMESPACE = "creative_asset"

_SECRET_LIKE_KEYS = (
    "secret",
    "token",
    "password",
    "credential",
    "api_key",
    "apikey",
    "authorization",
)


class CreativeMediaType(StrEnum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"


class CreativePurpose(StrEnum):
    PAID_AD = "PAID_AD"
    ORGANIC_VIDEO = "ORGANIC_VIDEO"


class CreativeAssetSource(StrEnum):
    GENERATED = "GENERATED"
    UPLOADED = "UPLOADED"
    EXISTING_PROVIDER = "EXISTING_PROVIDER"
    EXTERNAL_URL = "EXTERNAL_URL"


class CreativeAssetStatus(StrEnum):
    DRAFT = "DRAFT"
    GENERATING = "GENERATING"
    READY = "READY"
    FAILED = "FAILED"
    RETIRED = "RETIRED"


class CreativeReadinessStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class CreativeBriefView(BaseModel):
    id: UUID
    product_id: UUID
    action_id: UUID
    experiment_id: UUID
    play_id: UUID
    platform: DistributionPlatform
    purpose: CreativePurpose
    media_type: CreativeMediaType
    content: dict = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime


class CreativeAssetRegisterRequest(BaseModel):
    brief_id: UUID
    source: CreativeAssetSource
    status: CreativeAssetStatus = CreativeAssetStatus.DRAFT
    public_url: HttpUrl | None = None
    provider_asset_id: str | None = Field(default=None, min_length=1, max_length=300)
    mime_type: str | None = Field(default=None, min_length=1, max_length=120)
    width: int | None = Field(default=None, ge=1, le=20000)
    height: int | None = Field(default=None, ge=1, le=20000)
    duration_seconds: float | None = Field(default=None, gt=0, le=3600)
    provenance: dict = Field(default_factory=dict)
    failure_reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_reference(self) -> CreativeAssetRegisterRequest:
        if self.status == CreativeAssetStatus.READY:
            if self.public_url is None and not self.provider_asset_id:
                raise ValueError("READY CreativeAsset requires a public URL or provider asset ID")
        if self.source == CreativeAssetSource.EXTERNAL_URL and self.public_url is None:
            raise ValueError("EXTERNAL_URL source requires public_url")
        if self.source == CreativeAssetSource.EXISTING_PROVIDER and not self.provider_asset_id:
            raise ValueError("EXISTING_PROVIDER source requires provider_asset_id")
        if self.status == CreativeAssetStatus.FAILED and not self.failure_reason:
            raise ValueError("FAILED CreativeAsset requires failure_reason")
        if self.status != CreativeAssetStatus.FAILED and self.failure_reason:
            raise ValueError("failure_reason is only valid for FAILED CreativeAsset")
        return self


class CreativeAssetView(BaseModel):
    id: UUID
    product_id: UUID
    action_id: UUID
    brief_id: UUID
    brief_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    platform: DistributionPlatform
    purpose: CreativePurpose
    media_type: CreativeMediaType
    source: CreativeAssetSource
    status: CreativeAssetStatus
    public_url: HttpUrl | None = None
    provider_asset_id: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    provenance: dict = Field(default_factory=dict)
    failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class CreativeReadinessView(BaseModel):
    action_id: UUID
    brief: CreativeBriefView
    status: CreativeReadinessStatus
    selected_asset: CreativeAssetView | None = None
    reasons: list[str] = Field(default_factory=list)


class CreativeAssetService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def ensure_brief(self, action_id: UUID) -> CreativeBriefView:
        action = distribution_execution_service.get_action(action_id)
        if action.experiment_id is None:
            raise ValueError("DistributionAction has no experiment")
        experiment = distribution_execution_service.get_experiment(action.experiment_id)
        product = product_intake_service.get_product(experiment.product_id)

        if action.action_type == DistributionActionType.PAID_CAMPAIGN:
            spec = paid_campaign_spec_service.get(action_id)
            if spec is None:
                spec = paid_campaign_spec_service.ensure(action_id)
            purpose = CreativePurpose.PAID_AD
            media_type = (
                CreativeMediaType.VIDEO
                if action.platform == DistributionPlatform.TIKTOK
                else CreativeMediaType.IMAGE
            )
            content = {
                "product_name": spec.creative_brief.get("product_name") or product.name,
                "value_proposition": spec.creative_brief.get("value_proposition"),
                "message_hook": spec.creative_brief.get("message_hook"),
                "pain": spec.creative_brief.get("pain"),
                "desired_outcome": spec.creative_brief.get("desired_outcome"),
                "cta": spec.creative_brief.get("cta"),
                "audience": spec.audience,
            }
            constraints = [
                *[str(item) for item in spec.creative_brief.get("constraints", [])],
                "Use only confirmed product facts; do not fabricate testimonials or social proof.",
                "Avoid deceptive guarantees, fake urgency and unverifiable claims.",
                (
                    "Produce a provider-compatible "
                    f"{media_type.value.lower()} for {action.platform.value}."
                ),
            ]
        elif action.action_type == DistributionActionType.ORGANIC_VIDEO:
            purpose = CreativePurpose.ORGANIC_VIDEO
            media_type = CreativeMediaType.VIDEO
            content = {
                "product_name": product.name,
                "value_proposition": product.value_proposition or product.description,
                "script_or_brief": action.content_payload,
                "content_text": action.content_text,
            }
            constraints = [
                *[str(item) for item in product.constraints],
                "Use only confirmed product facts; do not fabricate testimonials or social proof.",
                "Avoid deceptive guarantees, fake urgency and unverifiable claims.",
                f"Produce a provider-compatible video for {action.platform.value}.",
            ]
        else:
            raise ValueError(
                "Creative assets are currently required only for PAID_CAMPAIGN and ORGANIC_VIDEO"
            )

        fingerprint_payload = {
            "product_id": str(product.id),
            "platform": action.platform.value,
            "purpose": purpose.value,
            "media_type": media_type.value,
            "content": content,
            "constraints": constraints,
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
        ).hexdigest()

        existing = self._brief_for_action(action_id)
        if existing is not None and existing.fingerprint == fingerprint:
            return existing

        brief = CreativeBriefView(
            id=uuid4(),
            product_id=product.id,
            action_id=action.id,
            experiment_id=experiment.id,
            play_id=experiment.distribution_play_id,
            platform=action.platform,
            purpose=purpose,
            media_type=media_type,
            content=content,
            constraints=constraints,
            fingerprint=fingerprint,
            created_at=datetime.now(UTC),
        )
        self._store.put(
            CREATIVE_BRIEF_NAMESPACE,
            str(brief.id),
            brief.model_dump(mode="json"),
        )
        self._store.put(
            CREATIVE_BRIEF_ACTION_NAMESPACE,
            str(action_id),
            {"brief_id": str(brief.id)},
        )
        return brief

    def get_brief(self, brief_id: UUID) -> CreativeBriefView:
        payload = self._store.get(CREATIVE_BRIEF_NAMESPACE, str(brief_id))
        if payload is None:
            raise KeyError(brief_id)
        return CreativeBriefView.model_validate(payload)

    def register_asset(self, payload: CreativeAssetRegisterRequest) -> CreativeAssetView:
        brief = self.get_brief(payload.brief_id)
        self._assert_safe_provenance(payload.provenance)
        now = datetime.now(UTC)
        asset = CreativeAssetView(
            id=uuid4(),
            product_id=brief.product_id,
            action_id=brief.action_id,
            brief_id=brief.id,
            brief_fingerprint=brief.fingerprint,
            platform=brief.platform,
            purpose=brief.purpose,
            media_type=brief.media_type,
            source=payload.source,
            status=payload.status,
            public_url=payload.public_url,
            provider_asset_id=(
                payload.provider_asset_id.strip() if payload.provider_asset_id else None
            ),
            mime_type=payload.mime_type,
            width=payload.width,
            height=payload.height,
            duration_seconds=payload.duration_seconds,
            provenance=payload.provenance,
            failure_reason=payload.failure_reason,
            created_at=now,
            updated_at=now,
        )
        self._store.put(
            CREATIVE_ASSET_NAMESPACE,
            str(asset.id),
            asset.model_dump(mode="json"),
        )
        return asset

    def get_asset(self, asset_id: UUID) -> CreativeAssetView:
        payload = self._store.get(CREATIVE_ASSET_NAMESPACE, str(asset_id))
        if payload is None:
            raise KeyError(asset_id)
        return CreativeAssetView.model_validate(payload)

    def list_assets(self, product_id: UUID) -> list[CreativeAssetView]:
        assets: list[CreativeAssetView] = []
        for payload in self._store.list_namespace(CREATIVE_ASSET_NAMESPACE):
            try:
                asset = CreativeAssetView.model_validate(payload)
            except ValueError:
                continue
            if asset.product_id == product_id:
                assets.append(asset)
        assets.sort(key=lambda item: (item.updated_at, str(item.id)), reverse=True)
        return assets

    def retire(self, asset_id: UUID) -> CreativeAssetView:
        asset = self.get_asset(asset_id)
        if asset.status == CreativeAssetStatus.RETIRED:
            return asset
        updated = asset.model_copy(
            update={
                "status": CreativeAssetStatus.RETIRED,
                "updated_at": datetime.now(UTC),
            }
        )
        self._store.put(
            CREATIVE_ASSET_NAMESPACE,
            str(updated.id),
            updated.model_dump(mode="json"),
        )
        return updated

    def readiness(self, action_id: UUID) -> CreativeReadinessView:
        brief = self.ensure_brief(action_id)
        candidates = [
            asset
            for asset in self.list_assets(brief.product_id)
            if asset.brief_fingerprint == brief.fingerprint
            and asset.media_type == brief.media_type
            and asset.purpose == brief.purpose
            and asset.platform == brief.platform
            and asset.status == CreativeAssetStatus.READY
        ]
        reasons: list[str] = []
        selected: CreativeAssetView | None = None
        for asset in candidates:
            provider_reasons = self._provider_readiness_reasons(brief, asset)
            if not provider_reasons:
                selected = asset
                break
            reasons.extend(provider_reasons)

        if selected is not None:
            return CreativeReadinessView(
                action_id=action_id,
                brief=brief,
                status=CreativeReadinessStatus.READY,
                selected_asset=selected,
                reasons=["A provider-ready action-level CreativeAsset is available."],
            )
        if not candidates:
            reasons.append(
                f"No READY {brief.media_type.value} CreativeAsset exists for this creative brief."
            )
        return CreativeReadinessView(
            action_id=action_id,
            brief=brief,
            status=CreativeReadinessStatus.BLOCKED,
            selected_asset=None,
            reasons=self._dedupe(reasons),
        )

    def _provider_readiness_reasons(
        self,
        brief: CreativeBriefView,
        asset: CreativeAssetView,
    ) -> list[str]:
        if brief.purpose == CreativePurpose.PAID_AD:
            if brief.platform == DistributionPlatform.INSTAGRAM and asset.public_url is None:
                return [
                    f"CreativeAsset {asset.id} is READY but Meta staging currently requires "
                    "a public image URL."
                ]
            if brief.platform == DistributionPlatform.TIKTOK and not asset.provider_asset_id:
                return [
                    f"CreativeAsset {asset.id} is READY but TikTok staging currently requires "
                    "a real provider video ID."
                ]
        if brief.purpose == CreativePurpose.ORGANIC_VIDEO:
            if asset.public_url is None and not asset.provider_asset_id:
                return [
                    f"CreativeAsset {asset.id} has no usable public or provider video reference."
                ]
        return []

    def _brief_for_action(self, action_id: UUID) -> CreativeBriefView | None:
        index = self._store.get(CREATIVE_BRIEF_ACTION_NAMESPACE, str(action_id))
        if not index or not index.get("brief_id"):
            return None
        try:
            return self.get_brief(UUID(str(index["brief_id"])))
        except (KeyError, ValueError):
            return None

    def _assert_safe_provenance(self, value: dict) -> None:
        def visit(item, path: str = "provenance") -> None:
            if isinstance(item, dict):
                for key, nested in item.items():
                    normalized = str(key).lower().replace("-", "_")
                    if any(marker in normalized for marker in _SECRET_LIKE_KEYS):
                        raise ValueError(
                            f"Secret-like provenance field is not allowed: {path}.{key}"
                        )
                    visit(nested, f"{path}.{key}")
            elif isinstance(item, list):
                for index, nested in enumerate(item):
                    visit(nested, f"{path}[{index}]")

        visit(value)

    def _dedupe(self, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            if value not in result:
                result.append(value)
        return result

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(CREATIVE_BRIEF_NAMESPACE)
            self._store.clear_namespace(CREATIVE_BRIEF_ACTION_NAMESPACE)
            self._store.clear_namespace(CREATIVE_ASSET_NAMESPACE)


creative_asset_service = CreativeAssetService()
