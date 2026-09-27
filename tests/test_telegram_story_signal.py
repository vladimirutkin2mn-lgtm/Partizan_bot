from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_profile_conversion_pack import TelegramProfilePackStatus
from app.telegram_story_signal import (
    TelegramStorySignalAttachRequest,
    TelegramStorySignalError,
    TelegramStorySignalService,
    TelegramStorySignalStatus,
)


def _pack(status=TelegramProfilePackStatus.APPLIED):
    return SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        product_id=uuid4(),
        action_id=uuid4(),
        experiment_id=uuid4(),
        status=status,
    )


class FakePackService:
    def __init__(self, pack):
        self.pack = pack

    def get(self, project_id, customer_token, pack_id):
        assert project_id == self.pack.project_id
        assert customer_token == "customer-token"
        assert pack_id == self.pack.id
        return self.pack


class FakeAnalyticsService:
    def __init__(self):
        self.events = []
        self.error = None

    def ingest_event(self, payload):
        if self.error is not None:
            raise self.error
        self.events.append(payload)
        return SimpleNamespace(event_id=payload.event_id)


def test_story_signal_requires_applied_profile_pack_and_explicit_confirmation() -> None:
    pack = _pack()
    service = TelegramStorySignalService(
        store=MemoryRuntimeStateStore(),
        pack_service=FakePackService(pack),
        analytics_service=FakeAnalyticsService(),
    )

    with pytest.raises(TelegramStorySignalError, match="Explicit"):
        service.attach(
            pack.project_id,
            "customer-token",
            pack.id,
            TelegramStorySignalAttachRequest(story_id=42, confirm_attach=False),
        )

    draft = _pack(status=TelegramProfilePackStatus.READY)
    service = TelegramStorySignalService(
        store=MemoryRuntimeStateStore(),
        pack_service=FakePackService(draft),
        analytics_service=FakeAnalyticsService(),
    )
    with pytest.raises(TelegramStorySignalError, match="Apply"):
        service.attach(
            draft.project_id,
            "customer-token",
            draft.id,
            TelegramStorySignalAttachRequest(story_id=42, confirm_attach=True),
        )


def test_story_signal_attaches_existing_customer_story_without_publishing() -> None:
    pack = _pack()
    service = TelegramStorySignalService(
        store=MemoryRuntimeStateStore(),
        pack_service=FakePackService(pack),
        analytics_service=FakeAnalyticsService(),
    )

    result = service.attach(
        pack.project_id,
        "customer-token",
        pack.id,
        TelegramStorySignalAttachRequest(story_id=42, confirm_attach=True),
    )

    assert result.status == TelegramStorySignalStatus.LINKED
    assert result.story_id == 42
    assert result.view_count == 0
    assert result.experiment_id == pack.experiment_id
    assert result.action_id == pack.action_id


def test_story_proxy_observation_ingests_cumulative_count_for_exact_experiment() -> None:
    pack = _pack()
    analytics = FakeAnalyticsService()
    service = TelegramStorySignalService(
        store=MemoryRuntimeStateStore(),
        pack_service=FakePackService(pack),
        analytics_service=analytics,
    )
    service.attach(
        pack.project_id,
        "customer-token",
        pack.id,
        TelegramStorySignalAttachRequest(story_id=42, confirm_attach=True),
    )

    observed = service.record_views_internal(
        pack.project_id,
        pack.id,
        story_id=42,
        cumulative_views=11,
    )

    assert observed.status == TelegramStorySignalStatus.OBSERVING
    assert observed.view_count == 11
    assert observed.attributed_view_count == 11
    assert observed.last_delta_views == 11
    assert observed.analytics_pending is False
    assert len(analytics.events) == 1
    event = analytics.events[0]
    assert event.event_type == "STORY_VIEW"
    assert event.experiment_id == pack.experiment_id
    assert event.action_id == pack.action_id
    assert event.properties["count"] == 11
    assert event.properties["delta"] == 11
    assert event.properties["story_id"] == 42


def test_repeated_story_observation_is_idempotent_at_same_count() -> None:
    pack = _pack()
    analytics = FakeAnalyticsService()
    service = TelegramStorySignalService(
        store=MemoryRuntimeStateStore(),
        pack_service=FakePackService(pack),
        analytics_service=analytics,
    )
    service.attach(
        pack.project_id,
        "customer-token",
        pack.id,
        TelegramStorySignalAttachRequest(story_id=42, confirm_attach=True),
    )

    service.record_views_internal(
        pack.project_id,
        pack.id,
        story_id=42,
        cumulative_views=4,
    )
    second = service.record_views_internal(
        pack.project_id,
        pack.id,
        story_id=42,
        cumulative_views=4,
    )

    assert second.view_count == 4
    assert second.last_delta_views == 0
    assert len(analytics.events) == 1


def test_story_proxy_keeps_provider_count_pending_until_experiment_is_measurable() -> None:
    pack = _pack()
    analytics = FakeAnalyticsService()
    service = TelegramStorySignalService(
        store=MemoryRuntimeStateStore(),
        pack_service=FakePackService(pack),
        analytics_service=analytics,
    )
    service.attach(
        pack.project_id,
        "customer-token",
        pack.id,
        TelegramStorySignalAttachRequest(story_id=42, confirm_attach=True),
    )

    analytics.error = ValueError("experiment not measurable")
    pending = service.record_views_internal(
        pack.project_id,
        pack.id,
        story_id=42,
        cumulative_views=6,
    )

    assert pending.view_count == 6
    assert pending.attributed_view_count == 0
    assert pending.analytics_pending is True
    assert "Analytics pending" in (pending.last_error or "")

    analytics.error = None
    recovered = service.record_views_internal(
        pack.project_id,
        pack.id,
        story_id=42,
        cumulative_views=6,
        expired=True,
    )

    assert recovered.status == TelegramStorySignalStatus.EXPIRED
    assert recovered.attributed_view_count == 6
    assert recovered.analytics_pending is False
    assert len(analytics.events) == 1
