"""Archived posts give provenance, never publishing rights or new search."""

from uuid import uuid4

import pytest

from app.femdom_archived_community_recovery import (
    PRODUCT_ID,
    _archived_public_communities,
    restore_fixture,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_community_registry import TelegramCommunityRegistry


def _seed_existing(store):
    registry = TelegramCommunityRegistry(store)
    registry.upsert(
        product_id=PRODUCT_ID,
        canonical_key="telegram:bizarre_afisha:community",
        title="Bizarre existing",
        url="https://t.me/bizarre_afisha",
        kind="CHANNEL",
        metadata={
            "handle": "bizarre_afisha",
            "community_fit_pass": True,
            "community_fit_reason": "VALID_NATIVE_FIT",
            "publisher_permission_verified": False,
        },
    )
    return registry


def test_archive_fixture_identifies_tenth_community_without_reviving_old_post():
    store = MemoryRuntimeStateStore()
    registry = _seed_existing(store)

    before = store.list_namespace("telegram_community_registry")
    preview = restore_fixture(store, product_id=PRODUCT_ID)
    assert store.list_namespace("telegram_community_registry") == before
    assert preview["unique_archive_handles"] == 2
    assert preview["already_registered"] == 1
    assert preview["would_stage"] == 1
    assert preview["llm_calls"] == 0

    result = restore_fixture(store, product_id=PRODUCT_ID, commit=True)
    assert result["newly_staged"] == 1
    assert [row["handle"] for row in result["rows"]] == [
        "bizarre_afisha", "freepeopleschedule"
    ]
    rows = registry.list_for_product(PRODUCT_ID)
    assert len(rows) == 2
    bizarre = next(
        r for r in rows if registry.hydrate_metadata(r)["handle"] == "bizarre_afisha"
    )
    free = next(
        r for r in rows if registry.hydrate_metadata(r)["handle"] == "freepeopleschedule"
    )
    assert bizarre["audience_fit"]["confirmed"] is True
    assert bizarre["title"] == "Bizarre existing"
    assert free["audience_fit"]["confirmed"] is False
    info = registry.hydrate_metadata(free)
    assert info["native_research_status"] == "UNVERIFIED"
    assert info["actionability"] == "RESEARCH_ONLY"
    assert info.get("action_target_url") is None
    assert info.get("action_target_specific") is not True
    assert info.get("publisher_permission_verified") is not True
    assert info["archived_source_post_urls"] == [
        "https://t.me/freepeopleschedule/523"
    ]
    repeated = restore_fixture(store, product_id=PRODUCT_ID, commit=True)
    assert repeated["newly_staged"] == 0
    assert len(registry.list_for_product(PRODUCT_ID)) == 2


def test_only_valid_public_provenance_is_imported():
    source = [
        {
            "platform": "TELEGRAM", "source": "OPERATIONAL_DISCOVERY",
            "url": "https://t.me/femdom_lovers/123",
        },
        {
            "platform": "TELEGRAM", "source": "OPERATIONAL_DISCOVERY",
            "url": "https://t.me/femdom_lovers/124",
        },
        {
            "platform": "TELEGRAM", "source": "OPERATIONAL_DISCOVERY",
            "url": "https://t.me/+PrivateHash123",
        },
        {
            "platform": "TELEGRAM", "source": "OPERATIONAL_DISCOVERY",
            "url": "http://t.me/evil_person/4",
        },
        {
            "platform": "TELEGRAM", "source": "OPERATIONAL_DISCOVERY",
            "url": "https://t.me/otherperson/4?wrong-query=a",
        },
        {
            "platform": "TELEGRAM", "source": "UNTRUSTED_SEARCH",
            "url": "https://t.me/untrusted_x/4",
        },
        {
            "platform": "REDDIT", "source": "OPERATIONAL_DISCOVERY",
            "url": "https://t.me/untrusted_x/4",
        },
    ]
    result = _archived_public_communities(source)
    assert sorted(result) == ["femdom_lovers", "otherperson"]
    assert result["femdom_lovers"]["source_post_urls"] == [
        "https://t.me/femdom_lovers/123",
        "https://t.me/femdom_lovers/124",
    ]


def test_scope_and_budget():
    store = MemoryRuntimeStateStore()
    with pytest.raises(ValueError, match="scoped"):
        restore_fixture(store, product_id=uuid4(), commit=True)
    with pytest.raises(ValueError, match="bounded"):
        _archived_public_communities([
            {"platform": "TELEGRAM", "source": "OPERATIONAL_DISCOVERY"}
            for _ in range(21)
        ])
    assert store.list_namespace("telegram_community_registry") == []


def test_live_queue_can_restore_other_historical_handles_but_never_old_targets():
    from app.customer_live_opportunities import (
        CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE,
        CUSTOMER_PROJECT_NAMESPACE,
    )

    store = MemoryRuntimeStateStore()
    _seed_existing(store)
    project_id = uuid4()
    other_product_project = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE, str(project_id),
        {"id": str(project_id), "product_id": str(PRODUCT_ID)},
    )
    store.put(
        CUSTOMER_PROJECT_NAMESPACE, str(other_product_project),
        {"id": str(other_product_project), "product_id": str(uuid4())},
    )
    for handle, owner in [
        ("new_femdom_peer", project_id),
        ("new_femdom_peer", project_id),
        ("wrong_product_peer", other_product_project),
    ]:
        key = f"{owner}:{handle}:{uuid4()}"
        store.put(CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE, key, {
            "project_id": str(owner),
            "url": f"https://t.me/{handle}/123",
            "platform": "TELEGRAM",
            "publishability": "READY",
            "status": "EXPIRED",
            "source": "OPERATIONAL_DISCOVERY",
        })
    preview = restore_fixture(store, product_id=PRODUCT_ID)
    assert preview["unique_archive_handles"] == 2
    assert preview["unique_live_queue_handles"] == 1
    assert preview["unique_combined_handles"] == 3
    assert preview["would_stage"] == 2  # FreePeople + other old real peer
    assert preview["telegram_calls"] == 0
    assert preview["llm_calls"] == 0
    applied = restore_fixture(store, product_id=PRODUCT_ID, commit=True)
    assert applied["newly_staged"] == 2

    registry = TelegramCommunityRegistry(store)
    peers = {
        registry.hydrate_metadata(row)["handle"]: row
        for row in registry.list_for_product(PRODUCT_ID)
    }
    assert sorted(peers) == ["bizarre_afisha", "freepeopleschedule", "new_femdom_peer"]
    meta = registry.hydrate_metadata(peers["new_femdom_peer"])
    assert meta["source_type"] == "HISTORICAL_LIVE_QUEUE"
    assert meta["archived_source_post_urls"] == [
        "https://t.me/new_femdom_peer/123"
    ]
    assert meta["actionability"] == "RESEARCH_ONLY"
    assert meta["native_research_status"] == "UNVERIFIED"
    assert meta["publisher_permission_verified"] is False
    assert meta["action_target_url"] is None
    assert peers["new_femdom_peer"]["audience_fit"]["confirmed"] is False
