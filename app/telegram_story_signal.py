from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, Field

from app.distribution_analytics_schemas import DistributionAnalyticsEventCreate
from app.distribution_analytics_service import distribution_analytics_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_profile_conversion_pack import (
    TelegramProfilePackStatus,
    telegram_profile_conversion_pack_service,
)

TELEGRAM_STORY_SIGNAL_NAMESPACE = "telegram_story_signal"


class TelegramStorySignalError(RuntimeError):
    pass


class TelegramStorySignalStatus(StrEnum):
    LINKED = "LINKED"
    OBSERVING = "OBSERVING"
    EXPIRED = "EXPIRED"


class TelegramStorySignalAttachRequest(BaseModel):
    story_id: int = Field(gt=0)
    confirm_attach: bool = False


class TelegramStorySignalView(BaseModel):
    id: UUID
    project_id: UUID
    product_id: UUID
    pack_id: UUID
    action_id: UUID
    experiment_id: UUID
    story_id: int = Field(gt=0)
    status: TelegramStorySignalStatus
    view_count: int = Field(default=0, ge=0)
    attributed_view_count: int = Field(default=0, ge=0)
    last_delta_views: int = Field(default=0, ge=0)
    analytics_pending: bool = False
    last_event_id: UUID | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
    last_observed_at: datetime | None = None


class TelegramStorySignalService:
    """Links an existing customer-owned Telegram story to a profile experiment.

    Story publication stays a separate operation. This service only binds a story
    identifier to an already-applied ProfileConversionPack and turns cumulative
    provider view counts into a proxy STORY_VIEW analytics signal.
    """

    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        pack_service=None,
        analytics_service=None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._packs = pack_service or telegram_profile_conversion_pack_service
        self._analytics = analytics_service or distribution_analytics_service

    def attach(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
        payload: TelegramStorySignalAttachRequest,
    ) -> TelegramStorySignalView:
        if not payload.confirm_attach:
            raise TelegramStorySignalError("Explicit story-signal attachment is required")
        pack = self._packs.get(project_id, customer_token, pack_id)
        if pack.status != TelegramProfilePackStatus.APPLIED:
            raise TelegramStorySignalError(
                "Apply the reviewed Telegram profile pack before attaching a story signal"
            )
        existing = self._for_pack(project_id, pack.id)
        if existing is not None:
            if existing.story_id != payload.story_id:
                raise TelegramStorySignalError(
                    "This profile pack is already linked to another Telegram story"
                )
            return existing

        now = datetime.now(UTC)
        record = {
            "id": str(uuid4()),
            "project_id": str(project_id),
            "product_id": str(pack.product_id),
            "pack_id": str(pack.id),
            "action_id": str(pack.action_id),
            "experiment_id": str(pack.experiment_id),
            "story_id": payload.story_id,
            "status": TelegramStorySignalStatus.LINKED.value,
            "view_count": 0,
            "attributed_view_count": 0,
            "last_delta_views": 0,
            "analytics_pending": False,
            "last_event_id": None,
            "last_error": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "last_observed_at": None,
        }
        self._persist(record)
        return self._view(record)

    def get(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
    ) -> TelegramStorySignalView:
        pack = self._packs.get(project_id, customer_token, pack_id)
        result = self._for_pack(project_id, pack.id)
        if result is None:
            raise TelegramStorySignalError(
                "No Telegram story signal is linked to this profile pack"
            )
        return result

    def record_views_internal(
        self,
        project_id: UUID,
        pack_id: UUID,
        *,
        story_id: int,
        cumulative_views: int,
        expired: bool = False,
    ) -> TelegramStorySignalView:
        if cumulative_views < 0:
            raise TelegramStorySignalError("Telegram story view count cannot be negative")
        current = self._for_pack(project_id, pack_id)
        if current is None:
            raise TelegramStorySignalError("Telegram story signal is not linked")
        if current.story_id != story_id:
            raise TelegramStorySignalError("Telegram story id does not match the linked story")

        now = datetime.now(UTC)
        observed = max(current.view_count, cumulative_views)
        attributed = current.attributed_view_count
        event_id = current.last_event_id
        analytics_pending = observed > attributed
        last_error = None

        if observed > attributed:
            event_id = uuid5(
                NAMESPACE_URL,
                f"partizan:telegram-story-view:{current.id}:{observed}",
            )
            try:
                self._analytics.ingest_event(
                    DistributionAnalyticsEventCreate(
                        event_id=event_id,
                        event_type="STORY_VIEW",
                        experiment_id=current.experiment_id,
                        action_id=current.action_id,
                        properties={
                            "count": observed,
                            "delta": observed - attributed,
                            "source": "telegram_story_proxy",
                            "story_id": current.story_id,
                            "profile_pack_id": str(current.pack_id),
                            "story_signal_id": str(current.id),
                        },
                    )
                )
                attributed = observed
                analytics_pending = False
            except (KeyError, ValueError) as exc:
                last_error = f"Analytics pending: {exc}"[:1000]

        record = {
            **self._payload(current.id),
            "status": (
                TelegramStorySignalStatus.EXPIRED.value
                if expired
                else TelegramStorySignalStatus.OBSERVING.value
            ),
            "view_count": observed,
            "attributed_view_count": attributed,
            "last_delta_views": max(0, observed - current.view_count),
            "analytics_pending": analytics_pending,
            "last_event_id": str(event_id) if event_id is not None else None,
            "last_error": last_error,
            "updated_at": now.isoformat(),
            "last_observed_at": now.isoformat(),
        }
        self._persist(record)
        return self._view(record)

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(TELEGRAM_STORY_SIGNAL_NAMESPACE)

    def _for_pack(
        self,
        project_id: UUID,
        pack_id: UUID,
    ) -> TelegramStorySignalView | None:
        matches: list[TelegramStorySignalView] = []
        for payload in self._store.list_namespace(TELEGRAM_STORY_SIGNAL_NAMESPACE):
            if (
                str(payload.get("project_id") or "") == str(project_id)
                and str(payload.get("pack_id") or "") == str(pack_id)
            ):
                try:
                    matches.append(self._view(payload))
                except ValueError:
                    continue
        matches.sort(key=lambda item: (item.updated_at, str(item.id)), reverse=True)
        return matches[0] if matches else None

    def _payload(self, signal_id: UUID) -> dict:
        payload = self._store.get(TELEGRAM_STORY_SIGNAL_NAMESPACE, str(signal_id))
        if payload is None:
            raise TelegramStorySignalError("Telegram story signal not found")
        return payload

    def _persist(self, payload: dict) -> None:
        self._store.put(
            TELEGRAM_STORY_SIGNAL_NAMESPACE,
            str(payload["id"]),
            payload,
        )

    @staticmethod
    def _view(payload: dict) -> TelegramStorySignalView:
        return TelegramStorySignalView.model_validate(payload)


telegram_story_signal_service = TelegramStorySignalService()
