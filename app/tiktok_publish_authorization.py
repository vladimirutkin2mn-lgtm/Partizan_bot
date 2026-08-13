from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.creative_assets import CreativeAssetSource, creative_asset_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.tiktok_owned_publishing import (
    TikTokCreatorPublishPreflightService,
    TikTokPrivacyLevel,
    tiktok_creator_publish_preflight_service,
)

TIKTOK_PUBLISH_AUTHORIZATION_NAMESPACE = "tiktok_publish_authorization"
TIKTOK_PUBLISH_AUTHORIZATION_ACTION_NAMESPACE = "tiktok_publish_authorization_action"


class TikTokPublishAuthorizationStatus(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    CONSUMED = "CONSUMED"
    REVOKED = "REVOKED"


class TikTokPublishAuthorizationCreateRequest(BaseModel):
    preflight_id: UUID
    title: str = Field(default="", max_length=4400)
    privacy_level: TikTokPrivacyLevel
    allow_comment: bool = False
    allow_duet: bool = False
    allow_stitch: bool = False
    commercial_content_enabled: bool = False
    brand_organic_toggle: bool = False
    brand_content_toggle: bool = False
    is_aigc: bool = False
    music_usage_confirmation_accepted: bool
    branded_content_policy_accepted: bool = False
    explicit_publish_consent: bool


class TikTokPublishAuthorizationView(BaseModel):
    id: UUID
    action_id: UUID
    product_id: UUID
    distribution_identity_id: UUID
    creative_asset_id: UUID
    preflight_id: UUID
    preflight_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    creator_username: str
    creator_nickname: str
    title: str
    privacy_level: TikTokPrivacyLevel
    allow_comment: bool
    allow_duet: bool
    allow_stitch: bool
    commercial_content_enabled: bool
    brand_organic_toggle: bool
    brand_content_toggle: bool
    is_aigc: bool
    music_usage_confirmation_accepted: bool
    branded_content_policy_accepted: bool
    explicit_publish_consent: bool
    consent_text_version: str = "tiktok-direct-post-v1"
    status: TikTokPublishAuthorizationStatus
    authorized_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None


class TikTokPublishAuthorizationService:
    def __init__(
        self,
        *,
        preflight_service: TikTokCreatorPublishPreflightService | None = None,
        store: RuntimeStateStore | None = None,
    ) -> None:
        self._preflight_service = (
            preflight_service or tiktok_creator_publish_preflight_service
        )
        self._store = store or get_runtime_store()

    def authorize(
        self,
        action_id: UUID,
        payload: TikTokPublishAuthorizationCreateRequest,
    ) -> TikTokPublishAuthorizationView:
        preflight = self._preflight_service.get_latest(action_id, require_fresh=True)
        if preflight.id != payload.preflight_id:
            raise ValueError(
                "Publish authorization must use the latest fresh TikTok creator preflight"
            )
        asset = creative_asset_service.get_asset(preflight.creative_asset_id)
        if asset.id != preflight.creative_asset_id or asset.action_id != action_id:
            raise ValueError("Publish authorization creative does not match the action")

        self._validate_title(payload.title)
        if payload.privacy_level not in preflight.privacy_level_options:
            raise ValueError(
                "Selected privacy level is not available in the fresh TikTok creator preflight"
            )
        if preflight.comment_disabled and payload.allow_comment:
            raise ValueError("TikTok creator settings currently disable comments")
        if preflight.duet_disabled and payload.allow_duet:
            raise ValueError("TikTok creator settings currently disable Duet")
        if preflight.stitch_disabled and payload.allow_stitch:
            raise ValueError("TikTok creator settings currently disable Stitch")

        if payload.commercial_content_enabled:
            if not payload.brand_organic_toggle and not payload.brand_content_toggle:
                raise ValueError(
                    "Commercial content requires Your Brand, Branded Content, or both"
                )
        elif payload.brand_organic_toggle or payload.brand_content_toggle:
            raise ValueError(
                "Commercial brand toggles require the commercial content disclosure setting"
            )
        if (
            payload.brand_content_toggle
            and payload.privacy_level == TikTokPrivacyLevel.SELF_ONLY
        ):
            raise ValueError("TikTok branded content cannot use SELF_ONLY privacy")
        if payload.brand_content_toggle and not payload.branded_content_policy_accepted:
            raise ValueError(
                "Branded Content Policy acceptance is required for branded content"
            )
        if not payload.music_usage_confirmation_accepted:
            raise ValueError("TikTok Music Usage Confirmation must be accepted by the creator")
        if not payload.explicit_publish_consent:
            raise ValueError("Explicit creator consent is required before TikTok publication")
        if asset.source == CreativeAssetSource.GENERATED and not payload.is_aigc:
            raise ValueError("AI-generated CreativeAsset requires TikTok AIGC disclosure")

        now = datetime.now(UTC)
        current = self._get_current_raw(action_id)
        if current is not None and current.status == TikTokPublishAuthorizationStatus.AUTHORIZED:
            revoked = current.model_copy(
                update={
                    "status": TikTokPublishAuthorizationStatus.REVOKED,
                    "revoked_at": now,
                }
            )
            self._persist(revoked)

        authorization = TikTokPublishAuthorizationView(
            id=uuid4(),
            action_id=action_id,
            product_id=preflight.product_id,
            distribution_identity_id=preflight.distribution_identity_id,
            creative_asset_id=preflight.creative_asset_id,
            preflight_id=preflight.id,
            preflight_fingerprint=preflight.fingerprint,
            creator_username=preflight.creator_username,
            creator_nickname=preflight.creator_nickname,
            title=payload.title,
            privacy_level=payload.privacy_level,
            allow_comment=payload.allow_comment,
            allow_duet=payload.allow_duet,
            allow_stitch=payload.allow_stitch,
            commercial_content_enabled=payload.commercial_content_enabled,
            brand_organic_toggle=payload.brand_organic_toggle,
            brand_content_toggle=payload.brand_content_toggle,
            is_aigc=payload.is_aigc,
            music_usage_confirmation_accepted=payload.music_usage_confirmation_accepted,
            branded_content_policy_accepted=payload.branded_content_policy_accepted,
            explicit_publish_consent=payload.explicit_publish_consent,
            status=TikTokPublishAuthorizationStatus.AUTHORIZED,
            authorized_at=now,
            expires_at=preflight.expires_at,
        )
        self._persist(authorization)
        self._store.put(
            TIKTOK_PUBLISH_AUTHORIZATION_ACTION_NAMESPACE,
            str(action_id),
            {"authorization_id": str(authorization.id)},
        )
        return authorization

    def get_current(
        self,
        action_id: UUID,
        *,
        require_usable: bool = False,
    ) -> TikTokPublishAuthorizationView:
        authorization = self._get_current_raw(action_id)
        if authorization is None:
            raise KeyError(action_id)
        if require_usable:
            if authorization.status != TikTokPublishAuthorizationStatus.AUTHORIZED:
                raise ValueError("TikTok publish authorization is no longer active")
            if authorization.expires_at <= datetime.now(UTC):
                raise ValueError("TikTok publish authorization has expired")
            preflight = self._preflight_service.get_latest(action_id, require_fresh=True)
            if (
                preflight.id != authorization.preflight_id
                or preflight.fingerprint != authorization.preflight_fingerprint
                or preflight.creative_asset_id != authorization.creative_asset_id
            ):
                raise ValueError(
                    "TikTok publish authorization no longer matches current provider evidence"
                )
        return authorization

    def consume(self, authorization_id: UUID) -> TikTokPublishAuthorizationView:
        authorization = self._get(authorization_id)
        if authorization.status != TikTokPublishAuthorizationStatus.AUTHORIZED:
            raise ValueError("TikTok publish authorization is not available for one-shot use")
        if authorization.expires_at <= datetime.now(UTC):
            raise ValueError("TikTok publish authorization has expired")
        current = self.get_current(authorization.action_id, require_usable=True)
        if current.id != authorization.id:
            raise ValueError("TikTok publish authorization has been superseded")
        updated = authorization.model_copy(
            update={
                "status": TikTokPublishAuthorizationStatus.CONSUMED,
                "consumed_at": datetime.now(UTC),
            }
        )
        self._persist(updated)
        return updated

    def revoke(self, action_id: UUID) -> TikTokPublishAuthorizationView:
        authorization = self.get_current(action_id)
        if authorization.status != TikTokPublishAuthorizationStatus.AUTHORIZED:
            return authorization
        updated = authorization.model_copy(
            update={
                "status": TikTokPublishAuthorizationStatus.REVOKED,
                "revoked_at": datetime.now(UTC),
            }
        )
        self._persist(updated)
        return updated

    def _validate_title(self, value: str) -> None:
        utf16_units = len(value.encode("utf-16-le")) // 2
        if utf16_units > 2200:
            raise ValueError("TikTok video title exceeds the 2200 UTF-16 unit limit")

    def _get_current_raw(self, action_id: UUID) -> TikTokPublishAuthorizationView | None:
        index = self._store.get(
            TIKTOK_PUBLISH_AUTHORIZATION_ACTION_NAMESPACE,
            str(action_id),
        )
        if not index or not index.get("authorization_id"):
            return None
        try:
            return self._get(UUID(str(index["authorization_id"])))
        except (KeyError, ValueError):
            return None

    def _get(self, authorization_id: UUID) -> TikTokPublishAuthorizationView:
        payload = self._store.get(
            TIKTOK_PUBLISH_AUTHORIZATION_NAMESPACE,
            str(authorization_id),
        )
        if payload is None:
            raise KeyError(authorization_id)
        return TikTokPublishAuthorizationView.model_validate(payload)

    def _persist(self, authorization: TikTokPublishAuthorizationView) -> None:
        self._store.put(
            TIKTOK_PUBLISH_AUTHORIZATION_NAMESPACE,
            str(authorization.id),
            authorization.model_dump(mode="json"),
        )

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(TIKTOK_PUBLISH_AUTHORIZATION_NAMESPACE)
            self._store.clear_namespace(TIKTOK_PUBLISH_AUTHORIZATION_ACTION_NAMESPACE)


tiktok_publish_authorization_service = TikTokPublishAuthorizationService()
