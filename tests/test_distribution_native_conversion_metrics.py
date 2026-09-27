from datetime import UTC, datetime
from uuid import uuid4

from app.distribution_analytics_schemas import DistributionCostBreakdownView
from app.distribution_analytics_service import (
    DistributionAttributedEvent,
    InMemoryDistributionAnalyticsService,
)
from app.runtime_store import MemoryRuntimeStateStore


def _event(event_type: str, *, count: int | None = None) -> DistributionAttributedEvent:
    properties = {}
    if count is not None:
        properties["count"] = count
    return DistributionAttributedEvent(
        event_id=uuid4(),
        experiment_id=uuid4(),
        event_type=event_type,
        actor_id=None,
        revenue=0,
        occurred_at=datetime.now(UTC),
        properties=properties,
        attributed_by="experiment_id",
    )


def test_native_telegram_join_bot_start_and_story_proxy_metrics_are_aggregated() -> None:
    service = InMemoryDistributionAnalyticsService(store=MemoryRuntimeStateStore())
    events = [
        _event("JOIN", count=3),
        _event("BOT_START"),
        _event("BOT_START"),
        _event("STORY_VIEW", count=7),
    ]

    metrics = service._metrics(events, DistributionCostBreakdownView())

    assert metrics.joins == 3
    assert metrics.bot_starts == 2
    assert metrics.story_views == 7
    assert metrics.visits == 0
    assert metrics.signups == 0


def test_cumulative_join_snapshots_use_highest_provider_count() -> None:
    service = InMemoryDistributionAnalyticsService(store=MemoryRuntimeStateStore())
    events = [
        _event("JOIN", count=2),
        _event("JOIN", count=5),
        _event("JOIN", count=4),
    ]

    metrics = service._metrics(events, DistributionCostBreakdownView())

    assert metrics.joins == 5


def test_cumulative_story_view_snapshots_use_highest_provider_count() -> None:
    service = InMemoryDistributionAnalyticsService(store=MemoryRuntimeStateStore())
    events = [
        _event("STORY_VIEW", count=3),
        _event("STORY_VIEW", count=9),
        _event("STORY_VIEW", count=8),
    ]

    metrics = service._metrics(events, DistributionCostBreakdownView())

    assert metrics.story_views == 9
