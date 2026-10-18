"""Offline tests for the bounded TGStat public catalog expansion path."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.community_first_audience_engine import (
    GROWTH_MANDATE_NAMESPACE,
    CommunityFirstAudienceIntelligenceEngine,
)
from app.community_first_telegram_adapter import CommunityFirstTelegramDiscoveryAdapter
from app.runtime_store import MemoryRuntimeStateStore
from app.search import OpenAIWebSearchProvider
from app.telegram_community_registry import (
    TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE,
    TelegramCommunityRegistry,
)
from app.telegram_tgstat_catalog import (
    TGSTAT_CATALOG_RUN_NAMESPACE,
    TGStatCatalogError,
    TGStatCatalogExpansion,
    _category_for,
    parse_tgstat_category_html,
)


def _product():
    return SimpleNamespace(
        id=uuid4(),
        name="FemDom",
        description="FemDom BDSM interest communities",
        problem_or_desire="Meet FemDom people",
        market="online",
        language="English",
        known_audience=["FemDom communities"],
    )


def _icp():
    return SimpleNamespace(
        id=uuid4(),
        title="FemDom people",
        description="BDSM hobbyists",
        pain="Finding FemDom discussion groups",
        trigger="Looking for community",
        alternatives=[],
    )


def _html(surface="chats"):
    kind = "chat" if surface == "chats" else "channel"
    return (
        "<html><body>"
        f'<a href="/ru/{kind}/@femdom_discussion">FemDom &amp; BDSM Discussion</a>'
        f'<a href="https://tgstat.com/ru/{kind}/@femdom_discussion">duplicate</a>'
        '<a href="/ru/chat/@unsafe_bot">bot</a>'
        '<a href="https://evil.example/ru/chat/@untrusted">external</a>'
        "</body></html>"
    )


class FakeSource:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    async def fetch(self, *, category, surface):
        self.calls.append((category, surface))
        if self.failure:
            raise self.failure
        return [
            {
                "username": "femdom_discussion",
                "title": "FemDom BDSM discussion",
                "type": "group" if surface == "chats" else "channel",
                "source_url": f"https://tgstat.com/ru/{'chat' if surface == 'chats' else 'channel'}/@femdom_discussion",
            }
        ]


class EmptyNative:
    def __init__(self):
        self.calls = 0

    async def discover(self, *, query, known_handles):
        self.calls += 1
        return []

    async def inspect_known(self, handles):
        self.calls += 1
        return []


class PaidSpy(OpenAIWebSearchProvider):
    def __init__(self):
        self.calls = 0

    async def search(self, discovery_query, limit=5):
        self.calls += 1
        return []


def _engine(store, product, catalog, native, paid):
    store.put(
        GROWTH_MANDATE_NAMESPACE,
        str(product.id),
        {"product_id": str(product.id), "status": "ACTIVE", "allowed_platforms": ["TELEGRAM"]},
    )
    engine = CommunityFirstAudienceIntelligenceEngine(
        paid,
        store=store,
        catalog_expansion=catalog,
    )
    for adapter in engine._all_adapters:
        if isinstance(adapter, CommunityFirstTelegramDiscoveryAdapter):
            adapter._research_connector = native
    return engine


def test_html_category_parser_preserves_peer_type_and_blocks_external_links():
    result = parse_tgstat_category_html(_html(), surface="chats")
    assert len(result) == 1
    assert result[0]["username"].lower() == "femdom_discussion"
    assert result[0]["type"] == "group"
    assert result[0]["source_url"] == "https://tgstat.com/ru/chat/@femdom_discussion"

    channels = parse_tgstat_category_html(_html("channels"), surface="channels")
    assert len(channels) == 1
    assert channels[0]["type"] == "channel"
    with pytest.raises(TGStatCatalogError, match="FORMAT_UNKNOWN"):
        parse_tgstat_category_html("<html><body>challenge</body></html>", surface="chats")


def test_no_category_skips_generic_or_guessed_catalog_requests():
    assert _category_for(SimpleNamespace(name="Unrelated"), []) is None
    assert _category_for(_product(), [_icp()]) == "erotica"


@pytest.mark.asyncio
async def test_disabled_source_makes_zero_calls_or_registry_writes():
    store, product = MemoryRuntimeStateStore(), _product()
    source = FakeSource()
    expander = TGStatCatalogExpansion(store, source=source, enabled=False)
    result = await expander.replenish(product, [_icp()], confirmed_count=0)
    assert result["status"] == "DISABLED"
    assert source.calls == []
    assert store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE) == []


@pytest.mark.asyncio
async def test_one_fetch_stages_unverified_then_rotates_surface_next_day():
    store, product = MemoryRuntimeStateStore(), _product()
    source = FakeSource()
    expander = TGStatCatalogExpansion(store, source=source, enabled=True)
    t = datetime(2026, 10, 18, 9, tzinfo=UTC)
    first = await expander.replenish(product, [_icp()], confirmed_count=0, now=t)
    again = await expander.replenish(
        product, [_icp()], confirmed_count=0, now=t + timedelta(hours=1)
    )
    second = await expander.replenish(
        product, [_icp()], confirmed_count=0, now=t + timedelta(hours=25)
    )

    assert first == {"status": "STAGED_UNVERIFIED", "staged_count": 1}
    assert again["status"] == "NOT_DUE"
    assert second["status"] == "STAGED_UNVERIFIED"
    assert source.calls == [("erotica", "chats"), ("erotica", "channels")]
    records = TelegramCommunityRegistry(store).list_for_product(product.id)
    assert len(records) == 1
    metadata = TelegramCommunityRegistry(store).hydrate_metadata(records[0])
    assert metadata.get("community_fit_pass") is not True
    assert metadata["actionability"] == "RESEARCH_ONLY"
    assert metadata["catalog_evidence_url"].startswith("https://tgstat.com/")
    assert store.get(TGSTAT_CATALOG_RUN_NAMESPACE, str(product.id))["source_requests"] == 1


@pytest.mark.asyncio
async def test_explicit_backoff_on_throttle_survives_next_day():
    store, product = MemoryRuntimeStateStore(), _product()
    source = FakeSource(TGStatCatalogError("SOURCE_THROTTLED", 172800))
    expander = TGStatCatalogExpansion(store, source=source, enabled=True)
    at = datetime(2026, 10, 18, 9, tzinfo=UTC)
    first = await expander.replenish(product, [_icp()], confirmed_count=0, now=at)
    blocked = await expander.replenish(
        product, [_icp()], confirmed_count=0, now=at + timedelta(hours=25)
    )
    assert first["status"] == "SOURCE_THROTTLED"
    assert blocked["status"] == "NOT_DUE"
    assert len(source.calls) == 1


@pytest.mark.asyncio
async def test_catalog_requires_sufficient_shortfall():
    store, product = MemoryRuntimeStateStore(), _product()
    source = FakeSource()
    result = await TGStatCatalogExpansion(
        store, source=source, enabled=True
    ).replenish(product, [_icp()], confirmed_count=3)
    assert result["status"] == "SUFFICIENT_REGISTRY"
    assert source.calls == []


@pytest.mark.asyncio
async def test_empty_registry_native_then_catalog_blocks_paid_web(monkeypatch):
    monkeypatch.setattr(
        "app.community_first_audience_engine.get_settings",
        lambda: SimpleNamespace(
            telegram_catalog_discovery_enabled=True,
            telegram_paid_discovery_enabled=True,
        ),
    )
    store, product = MemoryRuntimeStateStore(), _product()
    source, native, paid = FakeSource(), EmptyNative(), PaidSpy()
    expander = TGStatCatalogExpansion(store, source=source, enabled=True)
    engine = _engine(store, product, expander, native, paid)
    results = await engine.discover(product, [_icp()], max_opportunities=10)

    assert results == []  # Unverified catalog leads never become executable seeds.
    assert engine.last_catalog_status == "STAGED_UNVERIFIED"
    assert paid.calls == 0
    assert native.calls == 1
    assert len(TelegramCommunityRegistry(store).list_for_product(product.id)) == 1


@pytest.mark.asyncio
async def test_catalog_failure_does_not_trigger_paid_fallback(monkeypatch):
    monkeypatch.setattr(
        "app.community_first_audience_engine.get_settings",
        lambda: SimpleNamespace(
            telegram_catalog_discovery_enabled=True,
            telegram_paid_discovery_enabled=True,
        ),
    )
    store, product = MemoryRuntimeStateStore(), _product()
    source, native, paid = FakeSource(TGStatCatalogError("FORMAT_UNKNOWN")), EmptyNative(), PaidSpy()
    engine = _engine(
        store, product, TGStatCatalogExpansion(store, source=source, enabled=True), native, paid
    )
    assert await engine.discover(product, [_icp()]) == []
    assert engine.last_catalog_status == "FORMAT_UNKNOWN"
    assert paid.calls == 0
    assert len(store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE)) == 0


@pytest.mark.asyncio
async def test_disabled_growth_mandate_skips_native_catalog_and_web():
    store, product = MemoryRuntimeStateStore(), _product()
    store.put(
        GROWTH_MANDATE_NAMESPACE, str(product.id),
        {"product_id": str(product.id), "status": "ACTIVE", "allowed_platforms": []},
    )
    source, paid, native = FakeSource(), PaidSpy(), EmptyNative()
    engine = CommunityFirstAudienceIntelligenceEngine(
        paid,
        store=store,
        catalog_expansion=TGStatCatalogExpansion(store, source=source, enabled=True),
    )
    for adapter in engine._all_adapters:
        if isinstance(adapter, CommunityFirstTelegramDiscoveryAdapter):
            adapter._research_connector = native

    assert await engine.discover(product, [_icp()]) == []
    assert source.calls == []
    assert paid.calls == 0
    assert native.calls == 0
