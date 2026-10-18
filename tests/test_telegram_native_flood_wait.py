"""No-network regression tests for persistent Telegram FloodWait handling."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.community_first_telegram_adapter import (
    TELEGRAM_NATIVE_REPLENISH_NAMESPACE,
    CommunityFirstTelegramDiscoveryAdapter,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_community_registry import TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE
from app.telegram_native_flood_wait import (
    TELEGRAM_RESEARCH_FLOOD_WAIT_NAMESPACE,
    record_research_flood_wait,
    research_flood_wait_until,
)
from app.telegram_research import (
    TelegramFloodWaitError,
    TelegramKnownCommunityInspection,
)


def _product():
    return SimpleNamespace(
        id=uuid4(),
        name="FemDom",
        market="online",
        language="English",
        problem_or_desire="FemDom BDSM interests",
        description="Find FemDom and BDSM discussion communities",
        known_audience=["FemDom people"],
    )


def _icp():
    return SimpleNamespace(
        title="FemDom community members",
        pain="Finding suitable BDSM discussion",
        trigger="Looking for communities",
        alternatives=["BDSM discussion"],
    )


def _stored(store, product, handles):
    for index, handle in enumerate(handles):
        store.put(
            TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE,
            f"{product.id}:entity:{100 + index}",
            {
                "id": f"{product.id}:entity:{100 + index}",
                "product_id": str(product.id),
                "canonical_key": f"telegram:{100 + index}:community",
                "title": handle,
                "url": f"https://t.me/{handle}",
                "kind": "GROUP",
                "audience_fit": {"confirmed": True, "reason": "NATIVE_COMMUNITY_OVERLAP"},
                "native_snapshot": {
                    "handle": handle,
                    "telegram_entity_id": 100 + index,
                    "native_research_status": "VERIFIED",
                    "source_checked_at": "2026-10-01T00:00:00+00:00",
                },
                "metadata": {
                    "handle": handle,
                    "telegram_entity_id": 100 + index,
                    "community_fit_pass": True,
                },
            },
        )


class FloodOnce:
    def __init__(self, *, search=False):
        self.calls = 0
        self.search = search

    async def inspect_known(self, handles):
        self.calls += 1
        if self.calls == 1:
            raise TelegramFloodWaitError(72 * 3600)
        return [
            TelegramKnownCommunityInspection(handle, "RETRYABLE_FAILED")
            for handle in handles
        ]

    async def discover(self, *, query, known_handles):
        self.calls += 1
        if self.calls == 1:
            raise TelegramFloodWaitError(72 * 3600)
        return []


@pytest.mark.asyncio
async def test_flood_wait_preserves_full_duration_and_pending_known_batch():
    store, product = MemoryRuntimeStateStore(), _product()
    _stored(store, product, ["femdom_group0", "femdom_group1", "femdom_group2"])
    connector = FloodOnce()
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        research_connector=connector, store=store
    )
    start = datetime(2026, 10, 18, 8, tzinfo=UTC)

    first = await adapter.refresh_registered(product, [_icp()], now=start)
    assert connector.calls == 1
    assert {item.metadata["registry_refresh_status"] for item in first} == {
        "RETRYABLE_FAILED"
    }
    cursor = store.get("telegram_registered_native_refresh_cursor", str(product.id))
    assert cursor["next_index"] == 0
    assert cursor["last_batch_error_type"] == "TELEGRAM_FLOOD_WAIT"

    deferred = await adapter.refresh_registered(
        product, [_icp()], now=start + timedelta(hours=25)
    )
    assert connector.calls == 1
    assert {item.metadata["registry_refresh_status"] for item in deferred} == {
        "DEFERRED_FLOOD_WAIT"
    }
    assert all(item.metadata["registry_refresh_attempted"] is False for item in deferred)
    assert all(item.metadata["native_research_status"] == "VERIFIED" for item in deferred)

    resumed = await adapter.refresh_registered(
        product, [_icp()], now=start + timedelta(hours=73)
    )
    assert connector.calls == 2
    assert all(item.metadata["registry_refresh_attempted"] for item in resumed)
    assert research_flood_wait_until(store, now=start + timedelta(hours=73)) is None
    assert store.list_namespace(TELEGRAM_RESEARCH_FLOOD_WAIT_NAMESPACE) == []


@pytest.mark.asyncio
async def test_flood_wait_does_not_consume_other_products_daily_query_budget():
    store, first_product, other_product = MemoryRuntimeStateStore(), _product(), _product()
    connector = FloodOnce(search=True)
    start = datetime(2026, 10, 18, 8, tzinfo=UTC)
    first_adapter = CommunityFirstTelegramDiscoveryAdapter(
        research_connector=connector, store=store
    )
    other_adapter = CommunityFirstTelegramDiscoveryAdapter(
        research_connector=connector, store=store
    )

    assert await first_adapter.replenish_native(
        first_product, [_icp()], confirmed_count=0, now=start
    ) == []
    assert connector.calls == 1
    state = store.get(TELEGRAM_NATIVE_REPLENISH_NAMESPACE, str(first_product.id))
    assert state["last_status"] == "RETRYABLE_FAILED"
    assert state["last_error_type"] == "TELEGRAM_FLOOD_WAIT"
    assert state["recent_queries"] == {}
    assert datetime.fromisoformat(state["next_attempt_at"]) == (
        start + timedelta(hours=72)
    )

    assert await other_adapter.replenish_native(
        other_product, [_icp()], confirmed_count=0, now=start + timedelta(hours=25)
    ) == []
    assert connector.calls == 1
    assert store.get(
        TELEGRAM_NATIVE_REPLENISH_NAMESPACE, str(other_product.id)
    ) is None

    await first_adapter.replenish_native(
        first_product, [_icp()], confirmed_count=0, now=start + timedelta(hours=73)
    )
    assert connector.calls == 2
    assert store.get(TELEGRAM_NATIVE_REPLENISH_NAMESPACE, str(first_product.id))[
        "last_status"
    ] == "NO_FIT_MATCH"


def test_simultaneous_long_and_short_waits_do_not_shorten_each_other():
    store = MemoryRuntimeStateStore()
    start = datetime(2026, 10, 18, 8, tzinfo=UTC)
    record_research_flood_wait(store, 172800, now=start)
    record_research_flood_wait(store, 60, now=start)
    assert research_flood_wait_until(store, now=start) == start + timedelta(hours=48)
    assert research_flood_wait_until(store, now=start + timedelta(hours=49)) is None
    assert store.list_namespace(TELEGRAM_RESEARCH_FLOOD_WAIT_NAMESPACE) == []


def test_exception_keeps_rate_limit_seconds_without_external_io():
    error = TelegramFloodWaitError(220000)
    assert error.seconds == 220000
    assert str(error) == "TELEGRAM_FLOOD_WAIT"
