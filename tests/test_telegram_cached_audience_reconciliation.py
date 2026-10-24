"""Regression: semantic pilot confirmation must survive registry refresh."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.community_first_telegram_adapter import CommunityFirstTelegramDiscoveryAdapter
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_audience_semantic_fit import (
    AUDIENCE_FIT_CACHE_NAMESPACE,
    _fingerprint,
)
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_research import TelegramKnownCommunityInspection, TelegramResearchConnector


class _RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], int]] = []

    async def inspect_known(self, handles, *, recent_message_limit):
        self.calls.append((tuple(handles), recent_message_limit))
        return [
            TelegramKnownCommunityInspection(handle=h, status="RETRYABLE_FAILED")
            for h in handles
        ]

    async def discover_public(self, *args, **kwargs):
        raise AssertionError("No paid or global discovery in saved-peer refresh")


def _product():
    return SimpleNamespace(
        id=uuid4(),
        name="FemDom community",
        description="Adult FemDom community for curious Telegram readers",
        market="RU",
        language="Russian",
        problem_or_desire="Find a compatible community",
        value_proposition="Interesting discussions",
        known_audience=["BDSM community"],
    )


def _icp():
    return SimpleNamespace(
        title="People interested in FemDom",
        description="Interested in respectful adult communities",
        pain="Finding a niche discussion community",
        trigger="Wants to meet similar people",
        alternatives=[],
    )


def _seed(store, product, icp, handle, *, confirmed=False, verdict=None,
          cached_direct=False, age_days=0, wrong_fingerprint=False,
          native_status="VERIFIED"):
    registry = TelegramCommunityRegistry(store)
    about = "FemDom meetups and community discussion"
    row = registry.upsert(
        product_id=product.id,
        canonical_key=f"telegram:{handle}:community",
        title=handle,
        url=f"https://t.me/{handle}",
        kind="CHANNEL",
        metadata={
            "handle": handle,
            "about": about,
            "native_research_status": native_status,
            "source_checked_at": datetime.now(UTC).isoformat(),
            "community_fit_pass": confirmed,
            "community_fit_reason": "ALREADY_CONFIRMED" if confirmed else "",
            "actionability": "RESEARCH_ONLY",
            "publisher_permission_verified": False,
        },
    )
    if verdict:
        digest = _fingerprint(product, icp, title=handle, about=about)
        if wrong_fingerprint:
            digest = "0" * 64
        store.put(
            AUDIENCE_FIT_CACHE_NAMESPACE,
            f"{product.id}:{handle}",
            {
                "product_id": str(product.id),
                "handle": handle,
                "verdict": verdict,
                "direct_fit": cached_direct,
                "confidence": 0.98,
                "evidence": ["FemDom meetups"],
                "rationale": "Product and community audience directly match",
                "model": "gpt-6-luna",
                "prompt_version": "multilingual-fit-v2",
                "fingerprint": digest,
                "checked_at": (
                    datetime.now(UTC) - timedelta(days=age_days)
                ).isoformat(),
            },
        )
    return row


@pytest.mark.asyncio
async def test_five_confirmed_from_two_registry_plus_three_valid_cached_direct():
    store, product, icp = MemoryRuntimeStateStore(), _product(), _icp()
    _seed(store, product, icp, "confirmeda", confirmed=True)
    _seed(store, product, icp, "confirmedb", confirmed=True)
    for handle in ("findom_gospoja0", "moscowellebdsm1", "poiskrabovirabin"):
        _seed(store, product, icp, handle, verdict="DIRECT", cached_direct=True)
    for handle in ("a_sfera", "play_hard_community"):
        _seed(store, product, icp, handle, verdict="ADJACENT", cached_direct=False)
    for handle in ("znakomstvachannel", "poiskfanfikov"):
        _seed(store, product, icp, handle, verdict="UNRELATED", cached_direct=False)

    transport = _RecordingTransport()
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        research_connector=TelegramResearchConnector(
            transport, known_handle_limit=3, recent_message_limit=4
        ),
        store=store,
    )
    # The same cached decisions are reused BEFORE any native refresh call.
    await adapter.refresh_registered(product, [icp])

    registry = TelegramCommunityRegistry(store)
    rows = registry.list_for_product(product.id)
    confirmed = [
        row for row in rows
        if (row.get("audience_fit") or {}).get("confirmed") is True
    ]
    assert len(rows) == 9
    assert len(confirmed) == 5
    assert {
        row["metadata"]["handle"]
        for row in confirmed
    } == {
        "confirmeda", "confirmedb", "findom_gospoja0",
        "moscowellebdsm1", "poiskrabovirabin",
    }
    for row in confirmed:
        meta = registry.hydrate_metadata(row)
        assert meta.get("publisher_permission_verified") is not True
        assert meta.get("actionability") != "READY"

    # Regular rotation still requests ONLY the newest post for cached direct peers.
    await adapter.refresh_registered(product, [icp])
    await adapter.refresh_registered(product, [icp])
    limits = {
        handle: recent_limit
        for handles, recent_limit in transport.calls
        for handle in handles
    }
    assert all(
        limits[handle] == 1
        for handle in (
            "confirmeda", "confirmedb", "findom_gospoja0",
            "moscowellebdsm1", "poiskrabovirabin",
        )
    )
    assert all(
        limits[handle] == 4
        for handle in (
            "a_sfera", "play_hard_community",
            "znakomstvachannel", "poiskfanfikov",
        )
    )
    # Reconciliation is idempotent: four refreshes do not create new peers.
    await adapter.refresh_registered(product, [icp])
    assert len(registry.list_for_product(product.id)) == 9


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdict", "direct", "age_days", "wrong_hash", "native_status"),
    [
        ("ADJACENT", False, 0, False, "VERIFIED"),
        ("UNRELATED", False, 0, False, "VERIFIED"),
        ("DIRECT", False, 0, False, "VERIFIED"),
        ("DIRECT", True, 65, False, "VERIFIED"),
        ("DIRECT", True, 0, True, "VERIFIED"),
        ("DIRECT", True, 0, False, "UNVERIFIED"),
    ],
)
async def test_unverified_or_invalid_cache_never_promotes(
    verdict, direct, age_days, wrong_hash, native_status
):
    store, product, icp = MemoryRuntimeStateStore(), _product(), _icp()
    _seed(
        store, product, icp, "unknown_group", verdict=verdict,
        cached_direct=direct, age_days=age_days, wrong_fingerprint=wrong_hash,
        native_status=native_status,
    )
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        use_default_research_connector=False, store=store
    )
    adapter._product_id = product.id
    assert await adapter._reconcile_cached_direct_audiences(product, icp) == 0
    row = TelegramCommunityRegistry(store).list_for_product(product.id)[0]
    assert row["audience_fit"]["confirmed"] is False


@pytest.mark.asyncio
async def test_cached_direct_reconciliation_is_idempotent_and_preserves_no_send():
    store, product, icp = MemoryRuntimeStateStore(), _product(), _icp()
    _seed(store, product, icp, "qualified_channel", verdict="DIRECT", cached_direct=True)
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        use_default_research_connector=False, store=store
    )
    adapter._product_id = product.id
    assert await adapter._reconcile_cached_direct_audiences(product, icp) == 1
    assert await adapter._reconcile_cached_direct_audiences(product, icp) == 0
    row = TelegramCommunityRegistry(store).list_for_product(product.id)[0]
    assert row["audience_fit"]["reason"] == "SEMANTIC_DIRECT_FIT_CACHED"
    meta = TelegramCommunityRegistry(store).hydrate_metadata(row)
    assert meta.get("action_target_url") is None
    assert meta.get("action_target_specific") is not True
    assert meta["publisher_permission_verified"] is False
