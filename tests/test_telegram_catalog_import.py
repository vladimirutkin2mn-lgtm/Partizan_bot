from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from app.community_first_telegram_adapter import CommunityFirstTelegramDiscoveryAdapter
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_catalog_import import import_public_catalog, parse_public_catalog
from app.telegram_community_registry import (
    TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE,
    TelegramCommunityRegistry,
)
from app.telegram_research import (
    TelegramCommunitySnapshot,
    TelegramKnownCommunityInspection,
    TelegramSurfaceKind,
)


def _product():
    return SimpleNamespace(
        id=uuid4(),
        name="FemDom",
        description="FemDom BDSM community and education",
        problem_or_desire="Find FemDom communities",
        value_proposition="Connect with people who share interests",
        known_audience=["FemDom curious"],
        market="online",
        language="English",
    )


def _icp():
    return SimpleNamespace(
        id=uuid4(),
        title="FemDom people",
        pain="Searching for BDSM discussion groups",
        trigger="Searching for a community",
        alternatives=["BDSM chats"],
    )


def test_parser_accepts_export_shapes_and_removes_duplicates_and_private_links():
    parser_rows = [
        {
            "id": "1111",
            "title": "FemDom conversation",
            "username": "Femdom_group",
            "type": "supergroup",
            "participants_count": 800,
        },
        {"username": "@FEMDOM_GROUP", "title": "Duplicated"},
        {"username": "https://t.me/+secretinvitation", "title": "Private"},
        {"username": "danger_bot", "title": "Automated account"},
        {"username": "https://evil.example/channel", "title": "Unsafe URL"},
    ]
    a = parse_public_catalog(parser_rows, source="telegram-groups-parser")
    assert len(a) == 1
    assert a[0]["handle"] == "femdom_group"
    assert a[0]["kind"] == "GROUP"

    scraper = {
        "groups": [
            {
                "username": "bdsm_community",
                "title": "BDSM group",
                "description": "FemDom discussion",
                "members": 2000,
                "is_group": True,
            }
        ],
        "users": [{"username": "not_a_community", "first_name": "Private"}],
    }
    b = parse_public_catalog(scraper, source="telegram-chat-scraper")
    assert [row["handle"] for row in b] == ["bdsm_community"]
    assert all("members" not in row and "users" not in row for row in b)


def test_dry_run_does_not_touch_registry():
    store, product = MemoryRuntimeStateStore(), _product()
    candidates = parse_public_catalog(
        [{"username": "femdom_group", "title": "FemDom discussion", "type": "group"}],
        source="telegram-groups-parser",
    )
    result = import_public_catalog(
        store=store,
        product_id=product.id,
        source="telegram-groups-parser",
        communities=candidates,
    )
    assert result["mode"] == "DRY_RUN"
    assert result["registry_mutations"] == 0
    assert store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE) == []


def test_catalog_import_is_unverified_and_merges_by_public_handle():
    store, product = MemoryRuntimeStateStore(), _product()
    rows = parse_public_catalog(
        [{"username": "femdom_group", "title": "FemDom group", "type": "supergroup"}],
        source="telegram-groups-parser",
    )
    for _ in range(2):
        import_public_catalog(
            store=store,
            product_id=product.id,
            source="telegram-groups-parser",
            communities=rows,
            commit=True,
        )
    stored = TelegramCommunityRegistry(store).list_for_product(product.id)
    assert len(stored) == 1
    hydrated = TelegramCommunityRegistry(store).hydrate_metadata(stored[0])
    assert hydrated["native_research_status"] == "NOT_CHECKED"
    assert hydrated.get("community_fit_pass") is not True
    assert hydrated["actionability"] == "RESEARCH_ONLY"


class FakeInspector:
    def __init__(self, *, related=True):
        self.related = related
        self.calls = []

    async def inspect_known(self, handles):
        self.calls.append(tuple(handles))
        title = "FemDom BDSM discussion" if self.related else "Antique camera trading"
        return [
            TelegramKnownCommunityInspection(
                handle,
                "VERIFIED",
                TelegramCommunitySnapshot(
                    entity_id=777,
                    username=handle,
                    title=title,
                    kind=TelegramSurfaceKind.GROUP,
                    url=f"https://t.me/{handle}",
                    about=title,
                    source_checked_at=datetime.now(UTC),
                ),
            )
            for handle in handles
        ]

    async def discover(self, **kwargs):
        raise AssertionError("Catalog verification must not search for new groups")


@pytest.mark.asyncio
async def test_native_inspection_promotes_only_audience_fit_catalog_lead():
    store, product = MemoryRuntimeStateStore(), _product()
    rows = parse_public_catalog(
        [{"username": "femdom_group", "title": "FemDom discussion", "type": "group"}],
        source="telegram-groups-parser",
    )
    import_public_catalog(
        store=store, product_id=product.id,
        source="telegram-groups-parser", communities=rows, commit=True,
    )
    native = FakeInspector(related=True)
    adapter = CommunityFirstTelegramDiscoveryAdapter(research_connector=native, store=store)
    found = await adapter.refresh_registered(product, [_icp()])

    assert native.calls == [("femdom_group",)]
    assert len(found) == 1
    assert found[0].canonical_key == "telegram:777:community"
    assert found[0].metadata["native_research_status"] == "VERIFIED"
    assert found[0].metadata["community_fit_pass"] is True
    stored = TelegramCommunityRegistry(store).list_for_product(product.id)
    assert len(stored) == 1
    assert stored[0]["audience_fit"]["confirmed"] is True


@pytest.mark.asyncio
async def test_unrelated_native_catalog_lead_remains_research_only():
    store, product = MemoryRuntimeStateStore(), _product()
    rows = parse_public_catalog(
        [{"username": "camera_chat", "title": "Unverified listing", "type": "group"}],
        source="telegram-groups-parser",
    )
    import_public_catalog(
        store=store, product_id=product.id,
        source="telegram-groups-parser", communities=rows, commit=True,
    )
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        research_connector=FakeInspector(related=False), store=store
    )
    found = await adapter.refresh_registered(product, [_icp()])

    assert found[0].metadata["native_research_status"] == "VERIFIED"
    assert found[0].metadata["community_fit_pass"] is False
    assert found[0].metadata["actionability"] == "RESEARCH_ONLY"
    assert found[0].metadata["action_target_url"] is None
    assert TelegramCommunityRegistry(store).list_for_product(product.id)[0][
        "audience_fit"
    ]["confirmed"] is False


def test_reimport_does_not_downgrade_verified_native_snapshot():
    store, product = MemoryRuntimeStateStore(), _product()
    registry = TelegramCommunityRegistry(store)
    registry.upsert(
        product_id=product.id,
        canonical_key="telegram:777:community",
        title="Verified FemDom group",
        url="https://t.me/femdom_group",
        kind="GROUP",
        metadata={
            "handle": "femdom_group",
            "telegram_entity_id": 777,
            "native_research_status": "VERIFIED",
            "source_checked_at": datetime.now(UTC).isoformat(),
            "community_fit_pass": True,
            "community_fit_reason": "NATIVE_COMMUNITY_OVERLAP",
        },
    )
    rows = parse_public_catalog(
        [{"username": "FEMDOM_GROUP", "title": "Unverified listing", "type": "group"}],
        source="telegram-groups-parser",
    )
    import_public_catalog(
        store=store, product_id=product.id,
        source="telegram-groups-parser", communities=rows, commit=True,
    )
    stored = registry.list_for_product(product.id)
    assert len(stored) == 1
    metadata = registry.hydrate_metadata(stored[0])
    assert metadata["native_research_status"] == "VERIFIED"
    assert metadata["community_fit_pass"] is True
    assert stored[0]["canonical_key"] == "telegram:777:community"


def test_saved_femdom_opportunities_replay_is_read_only_and_unverified():
    """Read the repository's Oct 8 FemDom snapshot, not live production state."""
    fixture = (
        Path(__file__).resolve().parents[1]
        / "ops"
        / "femdom"
        / "live_opportunities_2026-10-08.json"
    )
    saved = json.loads(fixture.read_text(encoding="utf-8"))
    catalog_rows = []
    for item in saved:
        if item.get("platform") != "TELEGRAM":
            continue
        url = urlsplit(str(item.get("url") or ""))
        segments = [part for part in url.path.split("/") if part]
        if url.netloc == "t.me" and segments:
            catalog_rows.append(
                {"username": segments[0], "title": item.get("title"), "type": "channel"}
            )
    parsed = parse_public_catalog(catalog_rows, source="tgscan-export")
    assert len(parsed) == 2  # Three historical posts, two distinct communities.

    store, product = MemoryRuntimeStateStore(), _product()
    import_public_catalog(
        store=store, product_id=product.id,
        source="tgscan-export", communities=parsed, commit=True,
    )
    records = TelegramCommunityRegistry(store).list_for_product(product.id)
    assert len(records) == 2
    assert all(
        TelegramCommunityRegistry(store).hydrate_metadata(item)["native_research_status"]
        == "NOT_CHECKED"
        for item in records
    )
    assert all(
        TelegramCommunityRegistry(store).hydrate_metadata(item)["actionability"]
        == "RESEARCH_ONLY"
        for item in records
    )
