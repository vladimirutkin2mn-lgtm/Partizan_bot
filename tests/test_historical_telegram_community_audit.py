"""Realistic read-only sampling of historical communities, never new search or posts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.historical_telegram_community_audit import (
    audit,
    historical_communities,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_research import (
    SurfaceAvailability,
    TelegramCommunitySnapshot,
    TelegramFloodWaitError,
    TelegramKnownCommunityInspection,
    TelegramRecentContext,
    TelegramSurfaceKind,
)


def _seed(store, product_id):
    store.put(
        "telegram_community_registry", f"{product_id}:entity:111",
        {
            "id": f"{product_id}:entity:111",
            "product_id": str(product_id),
            "canonical_key": "telegram:111:community",
            "title": "Old forum",
            "url": "https://t.me/femdomtalks",
            "kind": "GROUP",
            "metadata": {
                "handle": "femdomtalks", "community_fit_pass": True,
                "source_checked_at": "2026-09-15T08:00:00+00:00",
            },
            "audience_fit": {"confirmed": True, "terms": ["femdom"]},
        },
    )
    store.put(
        "audience_distribution_map", str(product_id),
        {
            "product_id": str(product_id),
            "opportunities": [
                {
                    "platform": "TELEGRAM",
                    "url": "https://t.me/FemDomTalks",
                    "relevance_score": 0.96,
                    "metadata": {"handle": "femdomtalks"},
                },
                {
                    "platform": "TELEGRAM",
                    "url": "https://t.me/previous_kink_hub",
                    "relevance_score": 0.91,
                    "metadata": {"community_fit_pass": True},
                },
                {
                    "platform": "TELEGRAM",
                    "url": "https://t.me/+private-token",
                    "relevance_score": 0.9,
                    "metadata": {},
                },
                {"platform": "REDDIT", "url": "https://reddit.com/r/a"},
            ],
        },
    )


def _snapshot(handle, checked):
    return TelegramCommunitySnapshot(
        entity_id=111, username=handle, title="FemDom conversations",
        kind=TelegramSurfaceKind.GROUP, url=f"https://t.me/{handle}",
        reply_surface=SurfaceAvailability.AVAILABLE,
        source_checked_at=checked,
        recent_context=(
            TelegramRecentContext(
                message_id=300, text="Обсуждаем новый кофе и десерты",
                published_at=checked - timedelta(hours=3),
                url=f"https://t.me/{handle}/300",
            ),
            TelegramRecentContext(
                message_id=299, text="FemDom встреча: обсуждаем границы",
                published_at=checked - timedelta(days=1),
                url=f"https://t.me/{handle}/299",
            ),
            TelegramRecentContext(
                message_id=200, text="FemDom старый пост",
                published_at=checked - timedelta(days=25),
                url=f"https://t.me/{handle}/200",
            ),
        ),
    )


class ReadOnlySpy:
    _known_handle_limit = 3

    def __init__(self, checked):
        self.checked = checked
        self.inspected = []
        self.search_calls = 0

    async def inspect_known(self, handles):
        raise AssertionError("Multi-post inspection must not be used by the audit")

    async def inspect_latest_known(self, handles):
        self.inspected.extend(handles)
        results = []
        for handle in handles:
            snapshot = _snapshot(handle, self.checked)
            snapshot = replace(snapshot, recent_context=snapshot.recent_context[:1])
            results.append(TelegramKnownCommunityInspection(handle, "VERIFIED", snapshot))
        return results

    async def discover(self, *args, **kwargs):
        self.search_calls += 1
        raise AssertionError("No contacts.Search or paid discovery allowed")


@pytest.mark.asyncio
async def test_historical_records_from_both_namespaces_are_deduped_and_inspected_read_only():
    product_id = uuid4()
    store = MemoryRuntimeStateStore()
    _seed(store, product_id)
    inventory, summary = historical_communities(store, product_id)
    assert summary["registry_rows"] == 1
    assert summary["historical_audience_rows"] == 3
    assert summary["unique_public_communities"] == 2
    assert summary["present_in_both"] == 1
    assert summary["historical_only"] == 1
    assert summary["historical_nonpublic_or_missing"] == 1
    assert inventory[0]["handle"] == "femdomtalks"

    checked = datetime(2026, 10, 18, 19, tzinfo=UTC)
    connector = ReadOnlySpy(checked)
    before = dict(store._payloads)
    result = await audit(
        product_id,
        store=store,
        connector=connector,
        product=SimpleNamespace(name="FemDom", known_audience=[]),
        max_groups=2,
        now=checked,
    )
    assert result["mode"] == "READ_ONLY_LIVE_INSPECTION"
    assert result["post_read_policy"] == "LATEST_ONLY_ONE_PER_COMMUNITY"
    assert result["summary"]["actually_inspected"] == 2
    assert result["summary"]["fresh_posts_observed"] == 2
    assert result["summary"]["fresh_keyword_matches"] == 0
    assert result["summary"]["selected_latest_posts_keyword_matching"] == 0
    assert result["summary"]["publishable_without_separate_checks"] == 0
    assert len(connector.inspected) == 2
    assert connector.search_calls == 0
    assert store._payloads == before
    for item in result["communities"]:
        assert item["inspection"]["selected_target_is_fresh"] is True
        assert item["inspection"]["selected_target_keyword_match"] is False
        assert item["inspection"]["live_write_access_verified"] is False
        assert item["inspection"]["publication_authorized"] is False
        assert item["inspection"]["fresh_keyword_match_count"] == 0
        assert item["inspection"]["post_samples_read"] == 1


@pytest.mark.asyncio
async def test_zero_cost_inventory_only_and_connector_unavailable_are_explicit():
    product_id = uuid4()
    store = MemoryRuntimeStateStore()
    _seed(store, product_id)
    product = SimpleNamespace(name="FemDom", known_audience=[])
    inventory = await audit(
        product_id, store=store, connector=None, product=product,
        max_groups=0, now=datetime(2026, 10, 18, tzinfo=UTC),
    )
    assert inventory["mode"] == "INVENTORY_ONLY"
    assert inventory["summary"]["selected_public_communities"] == 0
    blocked = await audit(
        product_id, store=store, connector=None, product=product,
        max_groups=2, now=datetime(2026, 10, 18, tzinfo=UTC),
    )
    assert blocked["mode"] == "RESEARCH_CONNECTOR_UNAVAILABLE"
    assert blocked["summary"]["actually_inspected"] == 0
    assert all(x["inspection"]["status"] == "NOT_CHECKED" for x in blocked["communities"])


@pytest.mark.asyncio
async def test_floodwait_prevents_research_and_produces_no_store_writes():
    product_id = uuid4()
    store = MemoryRuntimeStateStore()
    _seed(store, product_id)
    now = datetime(2026, 10, 18, tzinfo=UTC)
    store.put(
        "telegram_research_flood_wait", "token",
        {"id": "token", "blocked_until": (now + timedelta(days=1)).isoformat()},
    )
    connector = ReadOnlySpy(now)
    before = dict(store._payloads)
    result = await audit(
        product_id, store=store, connector=connector,
        product=SimpleNamespace(name="FemDom"), max_groups=2, now=now,
    )
    assert result["mode"] == "DEFERRED_FLOOD_WAIT"
    assert connector.inspected == []
    assert store._payloads == before


@pytest.mark.asyncio
async def test_floodwait_during_inspection_halts_remainder_without_retry():
    class Failing:
        _known_handle_limit = 3
        calls = 0

        async def inspect_latest_known(self, handles):
            self.calls += 1
            raise TelegramFloodWaitError(3600)

    product_id = uuid4()
    store = MemoryRuntimeStateStore()
    _seed(store, product_id)
    now = datetime(2026, 10, 18, tzinfo=UTC)
    probe = Failing()
    result = await audit(
        product_id, store=store, connector=probe,
        product=SimpleNamespace(name="FemDom"), max_groups=2, now=now,
    )
    assert probe.calls == 1
    assert result["mode"] == "HALTED_FLOOD_WAIT"
    assert result["blocking_flood_wait_until"] == (now + timedelta(hours=1)).isoformat()
    assert all(x["inspection"]["status"] == "NOT_CHECKED" for x in result["communities"])


@pytest.mark.asyncio
async def test_never_falls_back_to_four_post_connector():
    class LegacyOnly:
        async def inspect_known(self, handles):
            raise AssertionError("Must not read multiple posts")

    product_id = uuid4()
    store = MemoryRuntimeStateStore()
    _seed(store, product_id)
    result = await audit(
        product_id, store=store, connector=LegacyOnly(),
        product=SimpleNamespace(name="FemDom"), max_groups=2,
        now=datetime(2026, 10, 18, tzinfo=UTC),
    )
    assert result["mode"] == "RESEARCH_CONNECTOR_UNAVAILABLE"
    assert result["summary"]["actually_inspected"] == 0
