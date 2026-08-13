from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, Field

from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_schemas import DistributionActionExecutionRequest
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import DistributionActionStatus
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.tiktok_direct_post import (
    TIKTOK_DIRECT_POST_ATTEMPT_NAMESPACE,
    TikTokDirectPostAttemptStatus,
    TikTokDirectPostAttemptView,
    TikTokDirectPostService,
    tiktok_direct_post_service,
)

TIKTOK_DIRECT_POST_RECONCILIATION_NAMESPACE = "tiktok_direct_post_reconciliation"
TIKTOK_DIRECT_POST_RECONCILIATION_ACTION_NAMESPACE = (
    "tiktok_direct_post_reconciliation_action"
)
_SECRET_ENV_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,120}$")


class TikTokPostStatusApiError(RuntimeError):
    pass


class TikTokProviderPostStatus(StrEnum):
    PROCESSING_UPLOAD = "PROCESSING_UPLOAD"
    PROCESSING_DOWNLOAD = "PROCESSING_DOWNLOAD"
    SEND_TO_USER_INBOX = "SEND_TO_USER_INBOX"
    PUBLISH_COMPLETE = "PUBLISH_COMPLETE"
    FAILED = "FAILED"


class TikTokPostStatusView(BaseModel):
    status: TikTokProviderPostStatus
    fail_reason: str | None = Field(default=None, max_length=200)
    public_post_ids: list[str] = Field(default_factory=list)
    uploaded_bytes: int | None = Field(default=None, ge=0)
    downloaded_bytes: int | None = Field(default=None, ge=0)


class TikTokDirectPostReconciliationStatus(StrEnum):
    PROCESSING = "PROCESSING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class TikTokDirectPostReconciliationView(BaseModel):
    action_id: UUID
    attempt_id: UUID
    provider_publish_id: str = Field(min_length=1, max_length=64)
    status: TikTokDirectPostReconciliationStatus
    provider_status: TikTokProviderPostStatus
    fail_reason: str | None = Field(default=None, max_length=200)
    public_post_ids: list[str] = Field(default_factory=list)
    uploaded_bytes: int | None = Field(default=None, ge=0)
    downloaded_bytes: int | None = Field(default=None, ge=0)
    checked_at: datetime


class TikTokPostStatusClient(Protocol):
    def fetch_status(
        self,
        *,
        access_token: str,
        publish_id: str,
    ) -> TikTokPostStatusView: ...


class HttpxTikTokPostStatusClient:
    endpoint = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self._timeout_seconds = timeout_seconds

    def fetch_status(
        self,
        *,
        access_token: str,
        publish_id: str,
    ) -> TikTokPostStatusView:
        try:
            response = httpx.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                json={"publish_id": publish_id},
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise TikTokPostStatusApiError("TikTok publish-status request failed") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise TikTokPostStatusApiError(
                "TikTok publish-status returned invalid JSON"
            ) from exc
        if not isinstance(body, dict):
            raise TikTokPostStatusApiError(
                "TikTok publish-status returned an invalid response"
            )
        error = body.get("error")
        code = str(error.get("code") or "") if isinstance(error, dict) else ""
        if response.status_code >= 400 or code != "ok":
            safe_code = code[:120] if code else f"http_{response.status_code}"
            raise TikTokPostStatusApiError(
                f"TikTok publish-status rejected the request ({safe_code})"
            )
        data = body.get("data")
        if not isinstance(data, dict):
            raise TikTokPostStatusApiError(
                "TikTok publish-status response contained no data"
            )
        raw_public_ids = data.get("publicaly_available_post_id")
        if raw_public_ids is None:
            public_ids: list[str] = []
        elif isinstance(raw_public_ids, list):
            public_ids = [str(value) for value in raw_public_ids if str(value)]
        else:
            raise TikTokPostStatusApiError(
                "TikTok publish-status returned invalid public post IDs"
            )
        try:
            status = TikTokProviderPostStatus(str(data.get("status") or ""))
        except ValueError as exc:
            raise TikTokPostStatusApiError(
                "TikTok publish-status returned an unknown provider status"
            ) from exc
        fail_reason = data.get("fail_reason")
        normalized_fail_reason = (
            str(fail_reason)[:200] if fail_reason is not None and str(fail_reason) else None
        )
        try:
            return TikTokPostStatusView(
                status=status,
                fail_reason=normalized_fail_reason,
                public_post_ids=public_ids,
                uploaded_bytes=data.get("uploaded_bytes"),
                downloaded_bytes=data.get("downloaded_bytes"),
            )
        except ValueError as exc:
            raise TikTokPostStatusApiError(
                "TikTok publish-status returned invalid progress data"
            ) from exc


class TikTokDirectPostReconciliationService:
    def __init__(
        self,
        *,
        client: TikTokPostStatusClient | None = None,
        direct_post_service: TikTokDirectPostService | None = None,
        store: RuntimeStateStore | None = None,
    ) -> None:
        self._client = client or HttpxTikTokPostStatusClient()
        self._direct_post_service = direct_post_service or tiktok_direct_post_service
        self._store = store or get_runtime_store()

    def reconcile(self, action_id: UUID) -> TikTokDirectPostReconciliationView:
        attempt = self._direct_post_service.get_latest(action_id)
        if attempt.provider_publish_id is None:
            raise ValueError(
                "TikTok Direct Post has no confirmed publish_id; manual provider reconciliation is required"
            )
        if attempt.status not in {
            TikTokDirectPostAttemptStatus.SUBMITTED,
            TikTokDirectPostAttemptStatus.RECONCILIATION_REQUIRED,
        }:
            latest = self._get_latest_raw(action_id)
            if latest is not None and latest.status in {
                TikTokDirectPostReconciliationStatus.PUBLISHED,
                TikTokDirectPostReconciliationStatus.FAILED,
            }:
                if latest.status == TikTokDirectPostReconciliationStatus.PUBLISHED:
                    self._ensure_action_executed(attempt, latest)
                return latest
            raise ValueError(
                "TikTok Direct Post attempt is not in a provider-reconcilable state"
            )

        identity = distribution_control_plane_service.get_identity(
            attempt.distribution_identity_id
        )
        token_env = str(identity.profile_config.get("access_token_env") or "").strip()
        if not _SECRET_ENV_PATTERN.fullmatch(token_env):
            raise ValueError(
                "TikTok publishing access-token env reference is missing or invalid"
            )
        access_token = os.getenv(token_env)
        if access_token is None or not access_token.strip():
            raise ValueError("TikTok publishing access-token secret is unavailable")

        provider = self._client.fetch_status(
            access_token=access_token.strip(),
            publish_id=attempt.provider_publish_id,
        )
        reconciliation = TikTokDirectPostReconciliationView(
            action_id=action_id,
            attempt_id=attempt.id,
            provider_publish_id=attempt.provider_publish_id,
            status=self._local_status(provider.status),
            provider_status=provider.status,
            fail_reason=provider.fail_reason,
            public_post_ids=provider.public_post_ids,
            uploaded_bytes=provider.uploaded_bytes,
            downloaded_bytes=provider.downloaded_bytes,
            checked_at=datetime.now(UTC),
        )
        self._persist(reconciliation)

        if reconciliation.status == TikTokDirectPostReconciliationStatus.FAILED:
            failed_attempt = attempt.model_copy(
                update={
                    "status": TikTokDirectPostAttemptStatus.REJECTED,
                    "provider_error_code": (
                        reconciliation.fail_reason or "provider_publish_failed"
                    )[:120],
                    "updated_at": datetime.now(UTC),
                }
            )
            self._store.put(
                TIKTOK_DIRECT_POST_ATTEMPT_NAMESPACE,
                str(failed_attempt.id),
                failed_attempt.model_dump(mode="json"),
            )
        elif reconciliation.status == TikTokDirectPostReconciliationStatus.PUBLISHED:
            self._ensure_action_executed(attempt, reconciliation)

        return reconciliation

    def get_latest(self, action_id: UUID) -> TikTokDirectPostReconciliationView:
        result = self._get_latest_raw(action_id)
        if result is None:
            raise KeyError(action_id)
        return result

    def _ensure_action_executed(
        self,
        attempt: TikTokDirectPostAttemptView,
        reconciliation: TikTokDirectPostReconciliationView,
    ) -> None:
        action = distribution_execution_service.get_action(attempt.action_id)
        if action.status == DistributionActionStatus.EXECUTED:
            return
        if action.status != DistributionActionStatus.APPROVED:
            raise ValueError(
                "TikTok provider confirmed publication but DistributionAction cannot be marked EXECUTED"
            )
        distribution_execution_service.mark_executed(
            attempt.action_id,
            DistributionActionExecutionRequest(
                external_reference=attempt.provider_publish_id,
                notes=(
                    "TikTok Direct Post provider confirmed PUBLISH_COMPLETE"
                    + (
                        f"; public post ids: {','.join(reconciliation.public_post_ids)}"
                        if reconciliation.public_post_ids
                        else ""
                    )
                ),
            ),
        )

    def _local_status(
        self,
        provider_status: TikTokProviderPostStatus,
    ) -> TikTokDirectPostReconciliationStatus:
        if provider_status == TikTokProviderPostStatus.PUBLISH_COMPLETE:
            return TikTokDirectPostReconciliationStatus.PUBLISHED
        if provider_status == TikTokProviderPostStatus.FAILED:
            return TikTokDirectPostReconciliationStatus.FAILED
        return TikTokDirectPostReconciliationStatus.PROCESSING

    def _get_latest_raw(
        self,
        action_id: UUID,
    ) -> TikTokDirectPostReconciliationView | None:
        index = self._store.get(
            TIKTOK_DIRECT_POST_RECONCILIATION_ACTION_NAMESPACE,
            str(action_id),
        )
        if not index or not index.get("checked_at_key"):
            return None
        payload = self._store.get(
            TIKTOK_DIRECT_POST_RECONCILIATION_NAMESPACE,
            str(index["checked_at_key"]),
        )
        if payload is None:
            return None
        return TikTokDirectPostReconciliationView.model_validate(payload)

    def _persist(self, result: TikTokDirectPostReconciliationView) -> None:
        key = f"{result.action_id}:{result.checked_at.isoformat()}"
        self._store.put(
            TIKTOK_DIRECT_POST_RECONCILIATION_NAMESPACE,
            key,
            result.model_dump(mode="json"),
        )
        self._store.put(
            TIKTOK_DIRECT_POST_RECONCILIATION_ACTION_NAMESPACE,
            str(result.action_id),
            {"checked_at_key": key},
        )

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(TIKTOK_DIRECT_POST_RECONCILIATION_NAMESPACE)
            self._store.clear_namespace(TIKTOK_DIRECT_POST_RECONCILIATION_ACTION_NAMESPACE)


tiktok_direct_post_reconciliation_service = TikTokDirectPostReconciliationService()
