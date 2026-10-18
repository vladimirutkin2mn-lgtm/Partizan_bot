"""Cost-bounded native replenishment of already-known Telegram communities.

No live Telegram sessions, web searches or outbound publication occur in these tests.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.community_first_audience_engine import (
    GROWTH_MANDATE_NAMESPACE,
    TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE,
    CommunityFirstAudienceIntelligenceEngine,
)
from app.community_first_telegram_adapter import (
    TELEGRAM_NATIVE_REPLENISH_NAMESPACE,
    CommunityFirstTelegramDiscoveryAdapter,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.search import OpenAIWebSearchProvider
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


@pytest.mark.asyncio
async def test_adaptive_hints_cannot_bypass_saved_registry_to_paid_web():
    store, product = MemoryRuntimeStateStore(), _product()
    _registered(store, product, "first_femdom_group", 41)
    native, web = FakeNative(), ForbiddenWeb()
    store.put(
        GROWTH_MANDATE_NAMESPACE,
        str(product.id),
        {"product_id": str(product.id), "status": "ACTIVE", "allowed_platforms": ["TELEGRAM"]},
    )
    engine = CommunityFirstAudienceIntelligenceEngine(
        web, store=store, telegram_hints=["BDSM conversations"], telegram_only=True
    )
    for adapter in engine._all_adapters:
        if isinstance(adapter, CommunityFirstTelegramDiscoveryAdapter):
            adapter._research_connector = native

    found = await engine.discover(product, [_icp()], max_opportunities=20)

    assert found
    assert len(native.search_queries) == 1
    assert web.calls == 0


class EmptyNative(FakeNative):
    async def discover(self, *, query, known_handles):
        assert known_handles == []
        self.search_queries.append(query)
        return []


class PaidSearchSpy(OpenAIWebSearchProvider):
    def __init__(self):
        self.calls = []

    async def search(self, discovery_query, limit=5):
        self.calls.append(discovery_query.query)
        return []


@pytest.mark.asyncio
async def test_empty_registry_bootstraps_from_native_before_paid_web():
    store, product, icp = MemoryRuntimeStateStore(), _product(), _icp()
    native, web = FakeNative(), ForbiddenWeb()
    engine = _engine(store, product, native, web)

    found = await engine.discover(product, [icp], max_opportunities=20)

    assert web.calls == 0
    assert len(native.search_queries) == 1
    assert {row.canonical_key for row in found} == {"telegram:91:community"}
    assert len(store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE)) == 1
    # The next run inspects the saved community, not the same native query.
    await engine.discover(product, [icp], max_opportunities=20)
    assert web.calls == 0
    assert len(native.search_queries) == 1


@pytest.mark.asyncio
async def test_empty_registry_no_results_does_not_spend_without_opt_in(monkeypatch):
    store, product = MemoryRuntimeStateStore(), _product()
    native, web = EmptyNative(), PaidSearchSpy()
    monkeypatch.setattr(
        "app.community_first_audience_engine.get_settings",
        lambda: SimpleNamespace(telegram_paid_discovery_enabled=False),
    )
    engine = _engine(store, product, native, web)

    found = await engine.discover(product, [_icp()], max_opportunities=20)

    assert found == []
    assert len(native.search_queries) == 1
    assert web.calls == []
    assert not store.list_namespace(TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE)


@pytest.mark.asyncio
async def test_explicit_paid_fallback_claims_at_most_one_call_per_day(monkeypatch):
    store, product = MemoryRuntimeStateStore(), _product()
    native, web = EmptyNative(), PaidSearchSpy()
    monkeypatch.setattr(
        "app.community_first_audience_engine.get_settings",
        lambda: SimpleNamespace(
            telegram_paid_discovery_enabled=True,
            telegram_paid_discovery_monthly_budget_usd="1.00",
            telegram_paid_discovery_query_reservation_usd="0.25",
        ),
    )
    engine = _engine(store, product, native, web)
    icps = [_icp(), _icp(), _icp()]

    assert await engine.discover(product, icps, max_opportunities=20) == []
    assert len(web.calls) == 1
    assert await engine.discover(product, icps, max_opportunities=20) == []
    assert len(web.calls) == 1
    claims = store.list_namespace(TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE)
    assert len(claims) == 1
    assert claims[0]["product_id"] == str(product.id)
    assert claims[0]["maximum_paid_queries"] == 1
    assert claims[0]["estimated_cost_reservation_usd"] == "0.25"
    assert claims[0]["actual_billed_cost_usd"] is None


@pytest.mark.asyncio
async def test_native_success_never_calls_opted_in_paid_search(monkeypatch):
    store, product = MemoryRuntimeStateStore(), _product()
    native, web = FakeNative(), PaidSearchSpy()
    monkeypatch.setattr(
        "app.community_first_audience_engine.get_settings",
        lambda: SimpleNamespace(telegram_paid_discovery_enabled=True),
    )
    engine = _engine(store, product, native, web)
    found = await engine.discover(product, [_icp()])

    assert found
    assert web.calls == []


@pytest.mark.asyncio
async def test_paid_web_does_not_replace_failed_native_discovery(monkeypatch):
    monkeypatch.setattr(
        "app.community_first_audience_engine.get_settings",
        lambda: SimpleNamespace(
            telegram_catalog_discovery_enabled=False,
            telegram_paid_discovery_enabled=True,
            telegram_paid_discovery_monthly_budget_usd="1",
            telegram_paid_discovery_query_reservation_usd="0.25",
        ),
    )
    store, product = MemoryRuntimeStateStore(), _product()
    native, paid = FakeNative(error=True), PaidSearchSpy()
    engine = _engine(store, product, native, paid)

    assert await engine.discover(product, [_icp()]) == []
    assert native.search_queries
    assert paid.calls == []
    assert engine.last_paid_fallback_gate == "NATIVE_SEARCH_NOT_COMPLETED"
    state = store.get(TELEGRAM_NATIVE_REPLENISH_NAMESPACE, str(product.id))
    assert state["last_status"] == "RETRYABLE_FAILED"
    assert store.list_namespace(TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE) == []


@pytest.mark.asyncio
async def test_paid_web_does_not_replace_missing_native_research(monkeypatch):
    monkeypatch.setattr(
        "app.community_first_audience_engine.get_settings",
        lambda: SimpleNamespace(
            telegram_catalog_discovery_enabled=False,
            telegram_paid_discovery_enabled=True,
            telegram_paid_discovery_monthly_budget_usd="1",
            telegram_paid_discovery_query_reservation_usd="0.25",
        ),
    )
    store, product = MemoryRuntimeStateStore(), _product()
    paid = PaidSearchSpy()
    engine = _engine(store, product, None, paid)

    assert await engine.discover(product, [_icp()]) == []
    assert paid.calls == []
    assert engine.last_paid_fallback_gate == "NATIVE_SEARCH_NOT_COMPLETED"


@pytest.mark.asyncio
async def test_paid_web_requires_recent_native_no_fit_not_stale_record(monkeypatch):
    monkeypatch.setattr(
        "app.community_first_audience_engine.get_settings",
        lambda: SimpleNamespace(
            telegram_catalog_discovery_enabled=False,
            telegram_paid_discovery_enabled=True,
            telegram_paid_discovery_monthly_budget_usd="1",
            telegram_paid_discovery_query_reservation_usd="0.25",
        ),
    )
    store, product = MemoryRuntimeStateStore(), _product()
    store.put(
        TELEGRAM_NATIVE_REPLENISH_NAMESPACE, str(product.id),
        {"last_status": "NO_FIT_MATCH", "last_attempt_at": "2020-01-01T00:00:00+00:00"},
    )
    paid = PaidSearchSpy()
    engine = _engine(store, product, None, paid)

    assert await engine.discover(product, [_icp()]) == []
    assert paid.calls == []
    assert engine.last_paid_fallback_gate == "NATIVE_SEARCH_NOT_COMPLETED"


@pytest.mark.asyncio
async def test_stage_queue_records_native_success_without_fake_paid_or_catalog():
    from app.telegram_discovery_stage_queue import TelegramDiscoveryStageQueue

    store, product = MemoryRuntimeStateStore(), _product()
    native, paid = FakeNative(), PaidSearchSpy()
    engine = _engine(store, product, native, paid)
    found = await engine.discover(product, [_icp()], max_opportunities=20)
    assert found
    snapshot = engine.last_telegram_stage_queue
    assert snapshot is not None and snapshot["complete"] is True
    assert snapshot == TelegramDiscoveryStageQueue.latest(store, product)
    states = {item["stage"]: item for item in snapshot["stages"]}
    assert states["registry"]["status"] == "SKIPPED_NO_CANDIDATES"
    assert states["native"]["status"] == "COMPLETED"
    assert states["native"]["candidate_count"] == 1
    assert states["catalog"]["status"] == "SKIPPED_SUFFICIENT"
    assert states["paid"]["status"] == "NOT_APPLICABLE"
    assert paid.calls == []


@pytest.mark.asyncio
async def test_stage_queue_separates_native_provider_failure_from_no_fit():
    store, product = MemoryRuntimeStateStore(), _product()
    native, paid = FakeNative(error=True), PaidSearchSpy()
    engine = _engine(store, product, native, paid)
    assert await engine.discover(product, [_icp()]) == []
    snapshot = engine.last_telegram_stage_queue
    states = {item["stage"]: item for item in snapshot["stages"]}
    assert snapshot["complete"] is True
    assert states["native"]["status"] == "FAILED_RETRYABLE"
    assert states["native"]["error_type"] == "RuntimeError"
    assert states["paid"]["status"] == "BLOCKED"
    assert states["paid"]["reason"] == "NATIVE_SEARCH_NOT_COMPLETED"
    assert paid.calls == []


@pytest.mark.asyncio
async def test_stage_queue_is_not_created_for_disabled_telegram_mandate():
    from app.telegram_discovery_stage_queue import TELEGRAM_DISCOVERY_STAGE_NAMESPACE

    store, product = MemoryRuntimeStateStore(), _product()
    store.put(
        GROWTH_MANDATE_NAMESPACE,
        str(product.id),
        {"product_id": str(product.id), "status": "ACTIVE", "allowed_platforms": []},
    )
    native, paid = FakeNative(), PaidSearchSpy()
    engine = CommunityFirstAudienceIntelligenceEngine(paid, store=store)
    for adapter in engine._all_adapters:
        if isinstance(adapter, CommunityFirstTelegramDiscoveryAdapter):
            adapter._research_connector = native

    assert await engine.discover(product, [_icp()]) == []
    assert engine.last_telegram_stage_queue is None
    assert store.list_namespace(TELEGRAM_DISCOVERY_STAGE_NAMESPACE) == []
    assert paid.calls == []
