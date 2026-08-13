from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, Field

from app.creative_assets import creative_asset_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_types import DistributionIdentityStatus
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.tiktok_owned_publishing import EnvironmentSecretResolver, SecretResolver
from app.tiktok_publish_authorization import (
    TikTokPrivacyLevel,
    TikTokPublishAuthorizationService,
    TikTokPublishAuthorizationView,
    tiktok_publish_authorization_service,
)

TIKTOK_DIRECT_POST_ATTEMPT_NAMESPACE = "tiktok_direct_post_attempt"
TIKTOK_DIRECT_POST_ACTION_NAMESPACE = "tiktok_direct_post_action"
_SECRET_ENV_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,120}$")


class TikTokDirectPostApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        ambiguous: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.ambiguous = ambiguous


class TikTokContentPostingAuditStatus(StrEnum):
    AUDITED = "AUDITED"
    UNAUDITED = "UNAUDITED"


class TikTokDirectPostAttemptStatus(StrEnum):
    STARTED = "STARTED"
    SUBMITTED = "SUBMITTED"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class TikTokDirectPostAttemptView(BaseModel):
    id: UUID
    action_id: UUID
    authorization_id: UUID
    distribution_identity_id: UUID
    creative_asset_id: UUID
    client_audit_status: TikTokContentPostingAuditStatus
    status: TikTokDirectPostAttemptStatus
    provider_publish_id: str | None = Field(default=None, min_length=1, max_length=64)
    provider_error_code: str | None = Field(default=None, min_length=1, max_length=120)
    started_at: datetime
    updated_at: datetime


class TikTokDirectPostClient(Protocol):
    def initialize_video(
        self,
        *,
        access_token: str,
        authorization: TikTokPublishAuthorizationView,
        video_url: str,
    ) -> str: ...


class HttpxTikTokDirectPostClient:
    endpoint = "https://open.tiktokapis.com/v2/post/publish/video/init/"

    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self._timeout_seconds = timeout_seconds

    def initialize_video(
        self,
        *,
        access_token: str,
        authorization: TikTokPublishAuthorizationView,
        video_url: str,
    ) -> str:
        payload = {
            "post_info": {
                "title": authorization.title,
                "privacy_level": authorization.privacy_level.value,
                "disable_duet": not authorization.allow_duet,
                "disable_comment": not authorization.allow_comment,
                "disable_stitch": not authorization.allow_stitch,
                "brand_content_toggle": authorization.brand_content_toggle,
                "brand_organic_toggle": authorization.brand_organic_toggle,
                "is_aigc": authorization.is_aigc,
            },
            "source_info": {
                "source": "PULL_FROM_URL",
                "video_url": video_url,
            },
        }
        try:
            response = httpx.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                json=payload,
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise TikTokDirectPostApiError(
                "TikTok Direct Post request has an ambiguous network result",
                code="network_error",
                ambiguous=True,
            ) from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise TikTokDirectPostApiError(
                "TikTok Direct Post returned an ambiguous non-JSON result",
                code="invalid_response",
                ambiguous=True,
            ) from exc
        if not isinstance(body, dict):
            raise TikTokDirectPostApiError(
                "TikTok Direct Post returned an ambiguous invalid result",
                code="invalid_response",
                ambiguous=True,
            )

        error = body.get("error")
        code = str(error.get("code") or "") if isinstance(error, dict) else ""
        if response.status_code >= 500:
            raise TikTokDirectPostApiError(
                "TikTok Direct Post returned a server error with an ambiguous result",
                code=(code[:120] or f"http_{response.status_code}"),
                ambiguous=True,
            )
        if code != "ok":
            raise TikTokDirectPostApiError(
                "TikTok Direct Post rejected the publish request",
                code=(code[:120] or f"http_{response.status_code}"),
                ambiguous=False,
            )
        if response.status_code >= 400:
            raise TikTokDirectPostApiError(
                "TikTok Direct Post rejected the publish request",
                code=f"http_{response.status_code}",
                ambiguous=False,
            )
        data = body.get("data")
        publish_id = str(data.get("publish_id") or "") if isinstance(data, dict) else ""
        if not publish_id or len(publish_id) > 64:
            raise TikTokDirectPostApiError(
                "TikTok Direct Post did not return a confirmed publish_id",
                code="missing_publish_id",
                ambiguous=True,
            )
        return publish_id


class TikTokDirectPostService:
    def __init__(
        self,
        *,
        client: TikTokDirectPostClient | None = None,
        authorization_service: TikTokPublishAuthorizationService | None = None,
        secret_resolver: SecretResolver | None = None,
        store: RuntimeStateStore | None = None,
    ) -> None:
        self._client = client or HttpxTikTokDirectPostClient()
        self._authorization_service = (
            authorization_service or tiktok_publish_authorization_service
        )
        self._secret_resolver = secret_resolver or EnvironmentSecretResolver()
        self._store = store or get_runtime_store()

    def submit(self, action_id: UUID) -> TikTokDirectPostAttemptView:
        existing = self._get_latest_raw(action_id)
        if existing is not None and existing.status in {
            TikTokDirectPostAttemptStatus.SUBMITTED,
            TikTokDirectPostAttemptStatus.RECONCILIATION_REQUIRED,
        }:
            return existing
        if existing is not None and existing.status == TikTokDirectPostAttemptStatus.STARTED:
            reconciled = existing.model_copy(
                update={
                    "status": TikTokDirectPostAttemptStatus.RECONCILIATION_REQUIRED,
                    "provider_error_code": "interrupted_after_attempt_started",
                    "updated_at": datetime.now(UTC),
                }
            )
            self._persist(reconciled)
            return reconciled

        current_authorization = self._authorization_service.get_current(action_id)
        if (
            existing is not None
            and existing.authorization_id == current_authorization.id
        ):
            return existing
        authorization = self._authorization_service.get_current(
            action_id,
            require_usable=True,
        )

        identity = distribution_control_plane_service.get_identity(
            authorization.distribution_identity_id
        )
        if identity.status != DistributionIdentityStatus.ACTIVE:
            raise ValueError("TikTok publishing Distribution Identity is not ACTIVE")
        provider = str(identity.profile_config.get("execution_provider") or "").strip().lower()
        if provider != "tiktok_content_posting":
            raise ValueError("TikTok publishing identity is not configured for Content Posting")
        token_env = str(identity.profile_config.get("access_token_env") or "").strip()
        if not _SECRET_ENV_PATTERN.fullmatch(token_env):
            raise ValueError("TikTok publishing access-token env reference is missing or invalid")
        access_token = self._secret_resolver.resolve(token_env)
        if access_token is None:
            raise ValueError("TikTok publishing access-token secret is unavailable")

        audit_status = self._audit_status(identity.profile_config)
        if (
            audit_status == TikTokContentPostingAuditStatus.UNAUDITED
            and authorization.privacy_level != TikTokPrivacyLevel.SELF_ONLY
        ):
            raise ValueError("Unaudited TikTok Content Posting clients require SELF_ONLY privacy")

        asset = creative_asset_service.get_asset(authorization.creative_asset_id)
        if asset.action_id != action_id or asset.public_url is None:
            raise ValueError("TikTok Direct Post requires the authorized action-level public video URL")
        video_url = str(asset.public_url)
        verified_prefix = str(identity.profile_config.get("verified_url_prefix") or "").strip()
        self._validate_verified_url(video_url, verified_prefix)

        now = datetime.now(UTC)
        attempt = TikTokDirectPostAttemptView(
            id=uuid4(),
            action_id=action_id,
            authorization_id=authorization.id,
            distribution_identity_id=identity.id,
            creative_asset_id=asset.id,
            client_audit_status=audit_status,
            status=TikTokDirectPostAttemptStatus.STARTED,
            started_at=now,
            updated_at=now,
        )
        self._persist(attempt)
        self._store.put(
            TIKTOK_DIRECT_POST_ACTION_NAMESPACE,
            str(action_id),
            {"attempt_id": str(attempt.id)},
        )

        try:
            consumed = self._authorization_service.consume(authorization.id)
        except (KeyError, ValueError):
            blocked = attempt.model_copy(
                update={
                    "status": TikTokDirectPostAttemptStatus.BLOCKED,
                    "provider_error_code": "authorization_invalidated_before_provider_call",
                    "updated_at": datetime.now(UTC),
                }
            )
            self._persist(blocked)
            return blocked

        try:
            publish_id = self._client.initialize_video(
                access_token=access_token,
                authorization=consumed,
                video_url=video_url,
            )
        except TikTokDirectPostApiError as exc:
            failed = attempt.model_copy(
                update={
                    "status": (
                        TikTokDirectPostAttemptStatus.RECONCILIATION_REQUIRED
                        if exc.ambiguous
                        else TikTokDirectPostAttemptStatus.REJECTED
                    ),
                    "provider_error_code": (exc.code or "provider_error")[:120],
                    "updated_at": datetime.now(UTC),
                }
            )
            self._persist(failed)
            return failed

        submitted = attempt.model_copy(
            update={
                "status": TikTokDirectPostAttemptStatus.SUBMITTED,
                "provider_publish_id": publish_id,
                "updated_at": datetime.now(UTC),
            }
        )
        self._persist(submitted)
        return submitted

    def get_latest(self, action_id: UUID) -> TikTokDirectPostAttemptView:
        attempt = self._get_latest_raw(action_id)
        if attempt is None:
            raise KeyError(action_id)
        return attempt

    def _audit_status(self, profile_config: dict) -> TikTokContentPostingAuditStatus:
        raw = str(profile_config.get("content_posting_audit_status") or "").strip().upper()
        try:
            return TikTokContentPostingAuditStatus(raw)
        except ValueError as exc:
            raise ValueError(
                "TikTok Content Posting identity must declare AUDITED or UNAUDITED audit status"
            ) from exc

    def _validate_verified_url(self, video_url: str, verified_prefix: str) -> None:
        candidate = urlsplit(video_url)
        prefix = urlsplit(verified_prefix)
        if (
            candidate.scheme != "https"
            or not candidate.netloc
            or candidate.username
            or candidate.password
        ):
            raise ValueError("TikTok PULL_FROM_URL video must be a direct HTTPS URL")
        if (
            prefix.scheme != "https"
            or not prefix.netloc
            or prefix.username
            or prefix.password
            or prefix.query
            or prefix.fragment
        ):
            raise ValueError("TikTok identity verified_url_prefix must be an HTTPS domain or path")
        if candidate.hostname != prefix.hostname or candidate.port != prefix.port:
            raise ValueError("TikTok video URL is outside the configured verified URL prefix")
        prefix_path = prefix.path.rstrip("/")
        if prefix_path and not (
            candidate.path == prefix_path or candidate.path.startswith(prefix_path + "/")
        ):
            raise ValueError("TikTok video URL is outside the configured verified URL prefix")

    def _get_latest_raw(self, action_id: UUID) -> TikTokDirectPostAttemptView | None:
        index = self._store.get(TIKTOK_DIRECT_POST_ACTION_NAMESPACE, str(action_id))
        if not index or not index.get("attempt_id"):
            return None
        payload = self._store.get(
            TIKTOK_DIRECT_POST_ATTEMPT_NAMESPACE,
            str(index["attempt_id"]),
        )
        if payload is None:
            return None
        return TikTokDirectPostAttemptView.model_validate(payload)

    def _persist(self, attempt: TikTokDirectPostAttemptView) -> None:
        self._store.put(
            TIKTOK_DIRECT_POST_ATTEMPT_NAMESPACE,
            str(attempt.id),
            attempt.model_dump(mode="json"),
        )

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(TIKTOK_DIRECT_POST_ATTEMPT_NAMESPACE)
            self._store.clear_namespace(TIKTOK_DIRECT_POST_ACTION_NAMESPACE)


tiktok_direct_post_service = TikTokDirectPostService()
