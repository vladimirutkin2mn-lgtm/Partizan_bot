from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.community_first_telegram_adapter import (
    CommunityFirstTelegramDiscoveryAdapter,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_community_registry import TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE
from app.telegram_research import (
    SurfaceAvailability,
    TelegramCommunitySnapshot,
    TelegramKnownCommunityInspection,
    TelegramResearchConnector,
    TelegramSurfaceKind,
)


def _product():
    return SimpleNamespace(id=uuid4(), market="US", language="English")


def _icp():
    return SimpleNamespace(
        title="Founders",
        pain="Want to meet peers",
        trigger="Looking for other founders",
        alternatives=[],
    )


def _stored(store, product_id, handle, entity_id, *, invite=False):
    store.put(
        TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE,
        f"{product_id}:entity:{entity_id}",
        {
            "id": f"{product_id}:entity:{entity_id}",
            "product_id": str(product_id),
            "canonical_key": f"telegram:{entity_id}:community",
            "title": f"Founders {handle}",
            "url": f"https://t.me/{handle}",
            "kind": "GROUP",
            "audience_fit": {
                "confirmed": True,
                "reason": "NATIVE_COMMUNITY_OVERLAP",
                "terms": ["founders"],
            },
            "native_snapshot": {
                "handle": handle,
                "telegram_entity_id": entity_id,
                "native_research_status": "VERIFIED",
                "source_checked_at": "2026-01-01T00:00:00+00:00",
                "action_target_url": f"https://t.me/{handle}/10",
                "surface_kind": "GROUP",
            },
            "metadata": {
                "handle": handle,
                "telegram_entity_id": entity_id,
                "community_fit_pass": True,
                "community_fit_terms": ["founders"],
                "access_state": "JOIN_REQUIRED" if invite else "PUBLIC",
            },
        },
    )


def _snapshot(handle, entity_id):
    return TelegramCommunitySnapshot(
        entity_id=entity_id,
        username=handle,
        title=f"Founders {handle}",
        kind=TelegramSurfaceKind.GROUP,
        url=f"https://t.me/{handle}",
        about="Community for founders",
        source_checked_at=datetime(2026, 10, 18, tzinfo=UTC),
        reply_surface=SurfaceAvailability.AVAILABLE,
        standalone_post_surface=SurfaceAvailability.AVAILABLE,
    )


class FakeKnownPeerConnector:
    def __init__(self, *, failed_handle=None):
        self.calls = []
        self.failed_handle = failed_handle

    async def inspect_known(self, handles):
        self.calls.append(tuple(handles))
        return [
            TelegramKnownCommunityInspection(
                handle,
                "RETRYABLE_FAILED" if handle == self.failed_handle else "VERIFIED",
                None if handle == self.failed_handle else _snapshot(handle, 100 + int(handle[-1])),
            )
            for handle in handles
        ]

    async def discover(self, **kwargs):
        raise AssertionError("known-peer refresh must not issue contacts.Search")


@pytest.mark.asyncio
async def test_registry_rotates_seven_peers_without_research_search_calls():
    store = MemoryRuntimeStateStore()
    product = _product()
    for i in range(7):
        _stored(store, product.id, f"founders{i}", 100 + i)
    native = FakeKnownPeerConnector(failed_handle="founders3")
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        research_connector=native, store=store
    )
    observed = []
    for _ in range(3):
        current = await adapter.refresh_registered(product, [_icp()])
        assert len(current) == 7
        observed.extend(native.calls[-1])
        states = {item.metadata["handle"]: item.metadata for item in current}
        for handle in native.calls[-1]:
            assert states[handle]["registry_refresh_attempted"] is True
            assert states[handle]["registry_refresh_status"] in {
                "VERIFIED", "RETRYABLE_FAILED"
            }
        for handle in set(states) - set(native.calls[-1]):
            assert states[handle]["registry_refresh_status"] == "DEFERRED"
            assert states[handle]["source_checked_at"] in {
                "2026-01-01T00:00:00+00:00",
                "2026-10-18T00:00:00+00:00",
            }

    assert native.calls == [
        ("founders0", "founders1", "founders2"),
        ("founders3", "founders4", "founders5"),
        ("founders6",),
    ]
    assert sorted(set(observed)) == [f"founders{i}" for i in range(7)]
    cursor = store.get("telegram_registered_native_refresh_cursor", str(product.id))
    assert cursor["next_index"] == 0
    assert len(store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE)) == 7
    # A failed direct lookup must not erase previously verified native state.
    failed = next(
        row
        for row in store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE)
        if row["native_snapshot"]["handle"] == "founders3"
    )
    assert failed["native_snapshot"]["source_checked_at"] == (
        "2026-01-01T00:00:00+00:00"
    )


@pytest.mark.asyncio
async def test_no_native_connector_retains_historical_snapshot_without_fake_refresh():
    store = MemoryRuntimeStateStore()
    product = _product()
    _stored(store, product.id, "founders0", 100)
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        store=store, use_default_research_connector=False
    )
    result = await adapter.refresh_registered(product, [_icp()])
    assert len(result) == 1
    assert result[0].metadata["native_research_status"] == "VERIFIED"
    assert result[0].metadata["registry_refresh_status"] == "INSPECTION_UNAVAILABLE"
    assert result[0].metadata["source_checked_at"] == "2026-01-01T00:00:00+00:00"
    assert store.get(
        "telegram_registered_native_refresh_cursor", str(product.id)
    ) is None


@pytest.mark.asyncio
async def test_unverified_invitation_is_not_read_as_public_peer():
    store = MemoryRuntimeStateStore()
    product = _product()
    _stored(store, product.id, "founders0", 100, invite=True)
    native = FakeKnownPeerConnector()
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        research_connector=native, store=store
    )
    result = await adapter.refresh_registered(product, [_icp()])
    assert len(result) == 1
    assert result[0].metadata["registry_refresh_status"] == "DEFERRED"
    assert native.calls == []


class FakeReadOnlyTransport:
    def __init__(self):
        self.calls = []

    async def inspect_known(self, handles, *, recent_message_limit):
        self.calls.append(("inspect_known", tuple(handles), recent_message_limit))
        return [TelegramKnownCommunityInspection(h, "RETRYABLE_FAILED") for h in handles]

    async def discover_public(self, *args, **kwargs):
        raise AssertionError("refresh must never invoke new Telegram search")


@pytest.mark.asyncio
async def test_connector_inspection_caps_peer_count_and_does_not_search():
    transport = FakeReadOnlyTransport()
    connector = TelegramResearchConnector(transport, known_handle_limit=3)
    results = await connector.inspect_known(
        ["@Founders0", "founders0", "founders1", "founders2", "founders3"]
    )
    assert [row.handle for row in results] == [
        "founders0", "founders1", "founders2"
    ]
    assert transport.calls == [
        ("inspect_known", ("founders0", "founders1", "founders2"), 4)
    ]
