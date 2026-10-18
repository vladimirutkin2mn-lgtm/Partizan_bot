"""Cost-bounded native replenishment of already-known Telegram communities.

No live Telegram sessions, web searches or outbound publication occur in these tests.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.community_first_audience_engine import (
    GROWTH_MANDATE_NAMESPACE,
    CommunityFirstAudienceIntelligenceEngine,
)
from app.community_first_telegram_adapter import (
    TELEGRAM_NATIVE_REPLENISH_NAMESPACE,
    CommunityFirstTelegramDiscoveryAdapter,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_community_registry import TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE
from app.telegram_research import (
    TelegramCommunitySnapshot,
    TelegramKnownCommunityInspection,
    TelegramSurfaceKind,
)


def _product():
    return SimpleNamespace(
        id=uuid4(),
        name="FemDom",
        problem_or_desire="FemDom BDSM communities",
        value_proposition="Find an interest-based community",
        description="FemDom communities and connection",
        known_audience=["BDSM curious people"],
        market="online",
        language="English",
    )


def _icp():
    return SimpleNamespace(
        id=uuid4(),
        rank=1,
        title="FemDom community members",
        description="BDSM community",
        pain="Finding suitable FemDom community",
        trigger="Looking for discussion",
        alternatives=["BDSM discussion groups"],
    )


def _snapshot(handle, entity_id):
    return TelegramCommunitySnapshot(
        entity_id=entity_id,
        username=handle,
        title=f"FemDom BDSM community {handle}",
        kind=TelegramSurfaceKind.GROUP,
        url=f"https://t.me/{handle}",
        about="FemDom BDSM community discussion and mutual support",
        source_checked_at=datetime.now(UTC),
    )


def _registered(store, product, handle, entity_id, *, verified=True, invite=False):
    store.put(
        TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE,
        f"{product.id}:entity:{entity_id}",
        {
            "id": f"{product.id}:entity:{entity_id}",
            "product_id": str(product.id),
            "canonical_key": f"telegram:{entity_id}:community",
            "title": f"FemDom BDSM community {handle}",
            "url": f"https://t.me/{handle}",
            "kind": "GROUP",
            "audience_fit": {"confirmed": True, "reason": "NATIVE_COMMUNITY_OVERLAP"},
            "native_snapshot": {
                "handle": handle,
                "telegram_entity_id": entity_id,
                "native_research_status": "VERIFIED" if verified else "NOT_CHECKED",
                "source_checked_at": "2026-10-01T00:00:00+00:00",
                "access_state": "JOIN_REQUIRED" if invite else "PUBLIC",
            },
            "metadata": {
                "handle": handle,
                "telegram_entity_id": entity_id,
                "community_fit_pass": True,
                "access_state": "JOIN_REQUIRED" if invite else "PUBLIC",
            },
        },
    )


class FakeNative:
    def __init__(self, *, error=False):
        self.search_queries = []
        self.inspected = []
        self.error = error

    async def inspect_known(self, handles):
        self.inspected.append(tuple(handles))
        inspections = []
        for handle in handles:
            entity_id = (
                41 + int(handle.rsplit("_", 1)[-1])
                if handle.startswith("saved_femdom_")
                else 41
            )
            inspections.append(
                TelegramKnownCommunityInspection(
                    handle, "VERIFIED", _snapshot(handle, entity_id)
                )
            )
        return inspections

    async def discover(self, *, query, known_handles):
        assert known_handles == []
        self.search_queries.append(query)
        if self.error:
            raise RuntimeError("simulated Telegram read error")
        return [_snapshot(f"new_femdom_group_{len(self.search_queries)}", 90 + len(self.search_queries))]


class ForbiddenWeb:
    def __init__(self):
        self.calls = 0

    async def search(self, discovery_query, limit=5):
        self.calls += 1
        raise AssertionError("No paid web search may run for a saved Telegram registry")


def _engine(store, product, native, web):
    store.put(
        GROWTH_MANDATE_NAMESPACE,
        str(product.id),
        {"product_id": str(product.id), "status": "ACTIVE", "allowed_platforms": ["TELEGRAM"]},
    )
    engine = CommunityFirstAudienceIntelligenceEngine(web, store=store)
    for adapter in engine._all_adapters:
        if isinstance(adapter, CommunityFirstTelegramDiscoveryAdapter):
            adapter._research_connector = native
    return engine


@pytest.mark.asyncio
async def test_sparse_saved_femdom_registry_uses_native_before_web():
    store = MemoryRuntimeStateStore()
    product, icp = _product(), _icp()
    _registered(store, product, "first_femdom_group", 41)
    native, web = FakeNative(), ForbiddenWeb()
    engine = _engine(store, product, native, web)

    found = await engine.discover(product, [icp], max_opportunities=20)

    assert web.calls == 0
    assert len(native.search_queries) == 1
    assert native.inspected == [("first_femdom_group",)]
    assert {"telegram:41:community", "telegram:91:community"} <= {
        row.canonical_key for row in found
    }
    rows = store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE)
    assert len(rows) == 2
    assert all(row["audience_fit"]["confirmed"] is True for row in rows)


@pytest.mark.asyncio
async def test_replenishment_budget_and_query_cache_rotate_without_repeat():
    store, product, native = MemoryRuntimeStateStore(), _product(), FakeNative()
    adapter = CommunityFirstTelegramDiscoveryAdapter(research_connector=native, store=store)
    icp = _icp()
    start = datetime(2026, 10, 18, 12, tzinfo=UTC)

    first = await adapter.replenish_native(product, [icp], confirmed_count=1, now=start)
    repeated = await adapter.replenish_native(
        product, [icp], confirmed_count=1, now=start + timedelta(hours=2)
    )
    second = await adapter.replenish_native(
        product, [icp], confirmed_count=1, now=start + timedelta(hours=25)
    )

    assert len(first) == len(second) == 1
    assert repeated == []
    assert len(native.search_queries) == 2
    assert native.search_queries[0] != native.search_queries[1]
    state = store.get(TELEGRAM_NATIVE_REPLENISH_NAMESPACE, str(product.id))
    assert state["last_status"] == "FOUND"
    assert len(state["recent_queries"]) == 2
    assert state["last_candidate_count"] == 1


@pytest.mark.asyncio
async def test_enough_verified_registry_peers_never_trigger_expansion():
    store, product = MemoryRuntimeStateStore(), _product()
    for n in range(3):
        _registered(store, product, f"saved_femdom_{n}", 41 + n)
    native, web = FakeNative(), ForbiddenWeb()
    engine = _engine(store, product, native, web)

    found = await engine.discover(product, [_icp()], max_opportunities=20)

    assert len(found) == 3
    assert native.search_queries == []
    assert web.calls == 0


@pytest.mark.asyncio
async def test_join_required_saved_peer_does_not_count_as_usable_coverage():
    store, product = MemoryRuntimeStateStore(), _product()
    _registered(store, product, "invite_only_community", 41, invite=True)
    native, web = FakeNative(), ForbiddenWeb()
    engine = _engine(store, product, native, web)

    await engine.discover(product, [_icp()])

    assert native.inspected == []
    assert len(native.search_queries) == 1
    assert web.calls == 0


@pytest.mark.asyncio
async def test_native_failure_is_retryable_and_never_falls_back_to_paid_web():
    store, product = MemoryRuntimeStateStore(), _product()
    _registered(store, product, "first_femdom_group", 41)
    native, web = FakeNative(error=True), ForbiddenWeb()
    engine = _engine(store, product, native, web)

    first = await engine.discover(product, [_icp()])
    second = await engine.discover(product, [_icp()])

    assert first and second
    assert len(native.search_queries) == 1
    assert web.calls == 0
    state = store.get(TELEGRAM_NATIVE_REPLENISH_NAMESPACE, str(product.id))
    assert state["last_status"] == "RETRYABLE_FAILED"
    assert state["last_error_type"] == "RuntimeError"
    assert len(store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE)) == 1
