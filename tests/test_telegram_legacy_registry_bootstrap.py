"""Legacy Telegram group reuse is cheap and must not bypass native verification."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.community_first_audience_engine import CommunityFirstAudienceIntelligenceEngine
from app.community_first_telegram_adapter import CommunityFirstTelegramDiscoveryAdapter
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_community_registry import TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE
from app.telegram_legacy_registry_bootstrap import stage_legacy_telegram_communities
from app.telegram_research import (
    TelegramCommunitySnapshot,
    TelegramKnownCommunityInspection,
    TelegramSurfaceKind,
)


def _save_old(store, product_id, handles):
    store.put(
        "audience_distribution_map", str(product_id),
        {
            "product_id": str(product_id),
            "opportunities": [
                {
                    "platform": "TELEGRAM", "url": f"https://t.me/{handle}",
                    "title": f"Historical {handle}", "kind": "GROUP",
                    "relevance_score": 99,
                    "metadata": {
                        "handle": handle, "community_fit_pass": True,
                        "native_research_status": "VERIFIED",
                        "actionability": "READY",
                        "action_target_url": f"https://t.me/{handle}/55",
                    },
                }
                for handle in handles
            ],
        },
    )


def test_old_scores_and_target_rights_never_become_native_approval():
    store, product = MemoryRuntimeStateStore(), uuid4()
    _save_old(store, product, ["bizarre_afisha", "play_hard_community", "a_sfera"])
    result = stage_legacy_telegram_communities(store, product)
    assert result["staged_research_only"] == 3
    rows = store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE)
    assert len(rows) == 3
    for row in rows:
        assert row["audience_fit"]["confirmed"] is not True
        meta = row["metadata"]
        assert meta["legacy_audience_map_lead"] is True
        assert meta["native_research_status"] == "UNVERIFIED"
        assert meta.get("community_fit_pass") is not True
        assert meta["action_target_url"] is None
        assert meta["action_target_specific"] is False
        assert meta["actionability"] == "RESEARCH_ONLY"
    repeated = stage_legacy_telegram_communities(store, product)
    assert repeated["staged_research_only"] == 0
    assert repeated["already_registered"] == 3
    assert len(store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE)) == 3


def test_old_private_bots_duplicates_and_another_product_are_excluded():
    store, product = MemoryRuntimeStateStore(), uuid4()
    store.put(
        "audience_distribution_map", str(product),
        {
            "opportunities": [
                {"platform": "TELEGRAM", "url": "https://t.me/+privateinvite"},
                {"platform": "TELEGRAM", "url": "https://t.me/some_news_bot"},
                {"platform": "TELEGRAM", "url": "https://t.me/old_femdom_hub"},
                {"platform": "TELEGRAM", "url": "https://t.me/old_femdom_hub/123"},
                {"platform": "REDDIT", "url": "https://t.me/other_community"},
                {
                    "platform": "TELEGRAM", "url": "https://t.me/secret_group",
                    "metadata": {"access_state": "JOIN_REQUIRED"},
                },
            ],
        },
    )
    assert stage_legacy_telegram_communities(store, uuid4())["staged_research_only"] == 0
    result = stage_legacy_telegram_communities(store, product)
    assert result["staged_research_only"] == 1
    assert result["skipped_nonpublic"] == 3
    assert result["skipped_not_telegram"] == 1
    rows = store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE)
    assert len(rows) == 1 and rows[0]["url"] == "https://t.me/old_femdom_hub"


class NativeReadOnly:
    def __init__(self):
        self.known = []
        self.queries = []

    async def inspect_known(self, handles):
        self.known.append(tuple(handles))
        return [
            TelegramKnownCommunityInspection(
                handle, "VERIFIED",
                TelegramCommunitySnapshot(
                    entity_id=100 + int(handle[-1]),
                    username=handle,
                    title=f"FemDom BDSM community {handle}",
                    kind=TelegramSurfaceKind.GROUP,
                    url=f"https://t.me/{handle}",
                    about="FemDom BDSM community discussion",
                ),
            )
            for handle in handles
        ]

    async def discover(self, *, query, known_handles):
        self.queries.append(query)
        raise AssertionError("The first migration pass must use known peers only")


class NoPaidWeb:
    def __init__(self):
        self.calls = 0

    async def search(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("No paid web calls for saved legacy groups")


@pytest.mark.asyncio
async def test_engine_stages_history_then_verifies_without_web_or_new_group_search():
    store, product_id = MemoryRuntimeStateStore(), uuid4()
    _save_old(store, product_id, [f"saved_femdom_{i}" for i in range(5)])
    store.put(
        "growth_mandate", str(product_id),
        {"product_id": str(product_id), "status": "ACTIVE", "allowed_platforms": ["TELEGRAM"]},
    )
    product = SimpleNamespace(
        id=product_id, name="FemDom", market="online", language="English",
        problem_or_desire="FemDom BDSM connections", value_proposition="Community",
        description="Find communities", known_audience=["BDSM FemDom"],
    )
    icp = SimpleNamespace(
        id=uuid4(), rank=1, title="FemDom audience", pain="Want FemDom community",
        trigger="Looking for groups", description="BDSM talks", alternatives=[],
    )
    native, web = NativeReadOnly(), NoPaidWeb()
    engine = CommunityFirstAudienceIntelligenceEngine(web, store=store)
    for adapter in engine._all_adapters:
        if isinstance(adapter, CommunityFirstTelegramDiscoveryAdapter):
            adapter._research_connector = native

    found = await engine.discover(product, [icp], max_opportunities=20)
    assert web.calls == 0
    assert native.queries == []
    assert native.known == [("saved_femdom_0", "saved_femdom_1", "saved_femdom_2")]
    assert engine.last_legacy_reuse["staged_research_only"] == 5
    assert len(store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE)) == 5
    assert all(
        row.metadata.get("native_research_status") == "VERIFIED"
        and row.metadata.get("community_fit_pass") is True
        for row in found
    )
    for row in store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE):
        if row["metadata"].get("handle") in {"saved_femdom_3", "saved_femdom_4"}:
            assert row["audience_fit"]["confirmed"] is not True
