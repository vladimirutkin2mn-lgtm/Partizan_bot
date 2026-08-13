from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, Field

from app.creative_assets import CreativeReadinessStatus, creative_asset_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import (
    DistributionActionStatus,
    DistributionActionType,
    DistributionIdentityStatus,
    DistributionPlatform,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store

TIKTOK_CREATOR_PREFLIGHT_NAMESPACE = "tiktok_creator_publish_preflight"
TIKTOK_CREATOR_PREFLIGHT_ACTION_NAMESPACE = "tiktok_creator_publish_preflight_action"
_SECRET_ENV_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,120}$")
_DEFAULT_PREFLIGHT_TTL_SECONDS = 300


class TikTokCreatorInfoApiError(RuntimeError):
    pass


class TikTokPrivacyLevel(StrEnum):
    PUBLIC_TO_EVERYONE = "PUBLIC_TO_EVERYONE"
    MUTUAL_FOLLOW_FRIENDS = "MUTUAL_FOLLOW_FRIENDS"
    FOLLOWER_OF_CREATOR = "FOLLOWER_OF_CREATOR"
    SELF_ONLY = "SELF_ONLY"


class TikTokCreatorInfo(BaseModel):
    creator_username: str = Field(min_length=1, max_length=300)
    creator_nickname: str = Field(min_length=1, max_length=300)
    creator_avatar_url: str | None = Field(default=None, max_length=2000)
    privacy_level_options: list[TikTokPrivacyLevel] = Field(min_length=1)
    comment_disabled: bool
    duet_disabled: bool
    stitch_disabled: bool
    max_video_post_duration_sec: int = Field(gt=0, le=36000)


class TikTokCreatorPublishPreflightView(BaseModel):
    id: UUID
    action_id: UUID
    product_id: UUID
    distribution_identity_id: UUID
    creative_asset_id: UUID
    creator_username: str
    creator_nickname: str
    creator_avatar_url: str | None = None
    privacy_level_options: list[TikTokPrivacyLevel]
    comment_disabled: bool
    duet_disabled: bool
    stitch_disabled: bool
    max_video_post_duration_sec: int
    provider: str = "tiktok-content-posting-api"
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    fetched_at: datetime
    expires_at: datetime


class TikTokCreatorInfoClient(Protocol):
    def query_creator_info(self, *, access_token: str) -> TikTokCreatorInfo: ...


class SecretResolver(Protocol):
    def resolve(self, name: str) -> str | None: ...


class EnvironmentSecretResolver:
    def resolve(self, name: str) -> str | None:
        value = os.getenv(name)
        return value if value and value.strip() else None


class HttpxTikTokCreatorInfoClient:
    endpoint = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"

    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self._timeout_seconds = timeout_seconds

    def query_creator_info(self, *, access_token: str) -> TikTokCreatorInfo:
        try:
            response = httpx.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise TikTokCreatorInfoApiError("TikTok creator-info request failed") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise TikTokCreatorInfoApiError("TikTok creator-info returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise TikTokCreatorInfoApiError("TikTok creator-info returned an invalid response")
        if response.status_code >= 400:
            raise TikTokCreatorInfoApiError(
                f"TikTok creator-info HTTP {response.status_code}"
            )
        error = body.get("error")
        code = str(error.get("code") or "") if isinstance(error, dict) else ""
        if code != "ok":
            safe_code = code[:120] if code else "unknown_error"
            raise TikTokCreatorInfoApiError(
                f"TikTok creator-info rejected the request ({safe_code})"
            )
        data = body.get("data")
        if not isinstance(data, dict):
            raise TikTokCreatorInfoApiError("TikTok creator-info response contained no data")
        try:
            return TikTokCreatorInfo.model_validate(data)
        except ValueError as exc:
            raise TikTokCreatorInfoApiError(
                "TikTok creator-info response was missing required publishing capabilities"
            ) from exc


class TikTokCreatorPublishPreflightService:
    def __init__(
        self,
        *,
        client: TikTokCreatorInfoClient | None = None,
        secret_resolver: SecretResolver | None = None,
        store: RuntimeStateStore | None = None,
        ttl_seconds: int = _DEFAULT_PREFLIGHT_TTL_SECONDS,
    ) -> None:
        if ttl_seconds < 30 or ttl_seconds > 3600:
            raise ValueError("TikTok creator preflight TTL must be between 30 and 3600 seconds")
        self._client = client or HttpxTikTokCreatorInfoClient()
        self._secret_resolver = secret_resolver or EnvironmentSecretResolver()
        self._store = store or get_runtime_store()
        self._ttl_seconds = ttl_seconds

    def refresh(self, action_id: UUID) -> TikTokCreatorPublishPreflightView:
        action = distribution_execution_service.get_action(action_id)
        if (
            action.platform != DistributionPlatform.TIKTOK
            or action.action_type != DistributionActionType.ORGANIC_VIDEO
        ):
            raise ValueError(
                "TikTok creator publishing preflight requires a TikTok ORGANIC_VIDEO action"
            )
        if action.status != DistributionActionStatus.APPROVED:
            raise ValueError("TikTok creator publishing preflight requires an APPROVED action")
        if action.distribution_identity_id is None:
            raise ValueError(
                "TikTok creator publishing requires an explicit Distribution Identity"
            )

        identity = distribution_control_plane_service.get_identity(
            action.distribution_identity_id
        )
        if identity.platform != DistributionPlatform.TIKTOK:
            raise ValueError("Distribution Identity platform does not match TikTok publishing")
        if identity.status != DistributionIdentityStatus.ACTIVE:
            raise ValueError("TikTok Distribution Identity must be ACTIVE")
        allowed_actions = {
            str(value) for value in identity.eligibility.get("allowed_actions", [])
        }
        if DistributionActionType.ORGANIC_VIDEO.value not in allowed_actions:
            raise ValueError("TikTok Distribution Identity does not allow ORGANIC_VIDEO")

        config = identity.profile_config
        provider = str(config.get("execution_provider") or "").strip().lower()
        if provider != "tiktok_content_posting":
            raise ValueError(
                "TikTok Distribution Identity must explicitly opt in to tiktok_content_posting"
            )
        token_env = str(config.get("access_token_env") or "").strip()
        if not _SECRET_ENV_PATTERN.fullmatch(token_env):
            raise ValueError("TikTok creator access-token env reference is missing or invalid")
        access_token = self._secret_resolver.resolve(token_env)
        if access_token is None:
            raise ValueError("TikTok creator access-token secret is unavailable")

        readiness = creative_asset_service.readiness(action_id)
        if (
            readiness.status != CreativeReadinessStatus.READY
            or readiness.selected_asset is None
        ):
            raise ValueError(
                "TikTok creator publishing requires a READY action-level video asset"
            )
        asset = readiness.selected_asset

        experiment = distribution_execution_service.get_experiment(
            readiness.brief.experiment_id
        )
        info = self._client.query_creator_info(access_token=access_token)
        if (
            asset.duration_seconds is not None
            and asset.duration_seconds > info.max_video_post_duration_sec
        ):
            raise ValueError(
                "READY video exceeds the connected creator's current maximum post duration"
            )

        now = datetime.now(UTC)
        fingerprint_payload = {
            "action_id": str(action.id),
            "identity_id": str(identity.id),
            "creative_asset_id": str(asset.id),
            "creator_username": info.creator_username,
            "privacy_level_options": [item.value for item in info.privacy_level_options],
            "comment_disabled": info.comment_disabled,
            "duet_disabled": info.duet_disabled,
            "stitch_disabled": info.stitch_disabled,
            "max_video_post_duration_sec": info.max_video_post_duration_sec,
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        snapshot = TikTokCreatorPublishPreflightView(
            id=uuid4(),
            action_id=action.id,
            product_id=experiment.product_id,
            distribution_identity_id=identity.id,
            creative_asset_id=asset.id,
            creator_username=info.creator_username,
            creator_nickname=info.creator_nickname,
            creator_avatar_url=info.creator_avatar_url,
            privacy_level_options=info.privacy_level_options,
            comment_disabled=info.comment_disabled,
            duet_disabled=info.duet_disabled,
            stitch_disabled=info.stitch_disabled,
            max_video_post_duration_sec=info.max_video_post_duration_sec,
            fingerprint=fingerprint,
            fetched_at=now,
            expires_at=now + timedelta(seconds=self._ttl_seconds),
        )
        self._store.put(
            TIKTOK_CREATOR_PREFLIGHT_NAMESPACE,
            str(snapshot.id),
            snapshot.model_dump(mode="json"),
        )
        self._store.put(
            TIKTOK_CREATOR_PREFLIGHT_ACTION_NAMESPACE,
            str(action.id),
            {"preflight_id": str(snapshot.id)},
        )
        return snapshot

    def get_latest(
        self,
        action_id: UUID,
        *,
        require_fresh: bool = False,
    ) -> TikTokCreatorPublishPreflightView:
        index = self._store.get(
            TIKTOK_CREATOR_PREFLIGHT_ACTION_NAMESPACE,
            str(action_id),
        )
        if not index or not index.get("preflight_id"):
            raise KeyError(action_id)
        payload = self._store.get(
            TIKTOK_CREATOR_PREFLIGHT_NAMESPACE,
            str(index["preflight_id"]),
        )
        if payload is None:
            raise KeyError(action_id)
        snapshot = TikTokCreatorPublishPreflightView.model_validate(payload)
        if require_fresh and snapshot.expires_at <= datetime.now(UTC):
            raise ValueError(
                "TikTok creator publishing preflight has expired; refresh creator info"
            )

        readiness = creative_asset_service.readiness(action_id)
        if (
            readiness.status != CreativeReadinessStatus.READY
            or readiness.selected_asset is None
            or readiness.selected_asset.id != snapshot.creative_asset_id
        ):
            raise ValueError(
                "TikTok creator publishing preflight no longer matches the selected READY creative"
            )
        return snapshot

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(TIKTOK_CREATOR_PREFLIGHT_NAMESPACE)
            self._store.clear_namespace(TIKTOK_CREATOR_PREFLIGHT_ACTION_NAMESPACE)


tiktok_creator_publish_preflight_service = TikTokCreatorPublishPreflightService()
