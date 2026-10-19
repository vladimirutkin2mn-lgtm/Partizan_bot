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
    TelegramFloodWaitError,
    TelegramKnownCommunityInspection,
    TelegramRecentContext,
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


class FirstBatchFailsConnector(FakeKnownPeerConnector):
    async def inspect_known(self, handles):
        self.calls.append(tuple(handles))
        if len(self.calls) == 1:
            raise RuntimeError("temporary native lookup failure")
        return [
            TelegramKnownCommunityInspection(
                handle,
                "VERIFIED",
                _snapshot(handle, 100 + int(handle[-1])),
            )
            for handle in handles
        ]


@pytest.mark.asyncio
async def test_inspector_wide_failure_does_not_starve_remaining_registry_handles():
    store = MemoryRuntimeStateStore()
    product = _product()
    for i in range(6):
        _stored(store, product.id, f"founders{i}", 100 + i)
    native = FirstBatchFailsConnector()
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        research_connector=native, store=store
    )

    first = await adapter.refresh_registered(product, [_icp()])
    states = {row.metadata["handle"]: row.metadata for row in first}
    for i in range(3):
        item = states[f"founders{i}"]
        assert item["registry_refresh_attempted"] is True
        assert item["registry_refresh_status"] == "RETRYABLE_FAILED"
        assert item["registry_refresh_error_type"] == "RuntimeError"
        assert item["source_checked_at"] == "2026-01-01T00:00:00+00:00"
    for i in range(3, 6):
        assert states[f"founders{i}"]["registry_refresh_status"] == "DEFERRED"

    cursor = store.get("telegram_registered_native_refresh_cursor", str(product.id))
    assert cursor["next_index"] == 3
    assert cursor["last_batch_error_type"] == "RuntimeError"
    assert set(cursor["last_batch_statuses"].values()) == {"RETRYABLE_FAILED"}

    second = await adapter.refresh_registered(product, [_icp()])
    states = {row.metadata["handle"]: row.metadata for row in second}
    assert native.calls == [
        ("founders0", "founders1", "founders2"),
        ("founders3", "founders4", "founders5"),
    ]
    for i in range(3, 6):
        assert states[f"founders{i}"]["registry_refresh_status"] == "VERIFIED"
    cursor = store.get("telegram_registered_native_refresh_cursor", str(product.id))
    assert cursor["next_index"] == 0
    assert cursor["last_batch_error_type"] is None

    # The failed first batch is retryable on the next rotation, not deleted.
    third = await adapter.refresh_registered(product, [_icp()])
    states = {row.metadata["handle"]: row.metadata for row in third}
    assert native.calls[-1] == ("founders0", "founders1", "founders2")
    assert states["founders0"]["registry_refresh_status"] == "VERIFIED"


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


class MixedBatchTransport:
    """No API or Telegram account; inspect_known logs the exact message limit."""

    def __init__(self, *, failure_limit=None, flood_on_limit=None):
        self.calls = []
        self.failure_limit = failure_limit
        self.flood_on_limit = flood_on_limit

    async def inspect_known(self, handles, *, recent_message_limit):
        self.calls.append((tuple(handles), recent_message_limit))
        if recent_message_limit == self.flood_on_limit:
            raise TelegramFloodWaitError(3600)
        if recent_message_limit == self.failure_limit:
            raise RuntimeError("read-only transport temporarily unavailable")
        return [
            TelegramKnownCommunityInspection(
                handle, "VERIFIED",
                TelegramCommunitySnapshot(
                    entity_id=100 + int(handle[-1]),
                    username=handle,
                    title=f"Founders {handle}",
                    kind=TelegramSurfaceKind.GROUP,
                    url=f"https://t.me/{handle}",
                    about="Community for founders",
                    reply_surface=SurfaceAvailability.AVAILABLE,
                    standalone_post_surface=SurfaceAvailability.AVAILABLE,
                    source_checked_at=datetime(2026, 10, 19, tzinfo=UTC),
                    recent_context=tuple(
                        TelegramRecentContext(
                            message_id=501 - index,
                            text=f"Post {501 - index} from {handle}",
                            published_at=datetime(2026, 10, 19, tzinfo=UTC),
                            url=f"https://t.me/{handle}/{501 - index}",
                        )
                        for index in range(recent_message_limit)
                    ),
                ),
            )
            for handle in handles
        ]

    async def discover_public(self, *args, **kwargs):
        raise AssertionError("Existing community inspection must not search new groups")


@pytest.mark.asyncio
async def test_mixed_batch_reads_one_latest_per_confirmed_peer():
    store, product = MemoryRuntimeStateStore(), _product()
    for i in range(3):
        _stored(store, product.id, f"founders{i}", 100 + i)
    key = f"{product.id}:entity:101"
    unconfirmed = store.get(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE, key)
    unconfirmed["audience_fit"] = {"confirmed": False}
    unconfirmed["metadata"]["community_fit_pass"] = False
    store.put(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE, key, unconfirmed)
    transport = MixedBatchTransport()
    connector = TelegramResearchConnector(transport, known_handle_limit=3)
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        research_connector=connector, store=store
    )

    candidates = await adapter.refresh_registered(product, [_icp()])
    assert transport.calls == [
        (("founders0", "founders2"), 1),
        (("founders1",), 4),
    ]
    refreshed = {c.metadata["handle"]: c for c in candidates}
    for handle in ("founders0", "founders2"):
        assert refreshed[handle].metadata["registry_refresh_status"] == "VERIFIED"
        assert refreshed[handle].metadata["action_target_url"] == f"https://t.me/{handle}/501"
        assert refreshed[handle].metadata["action_target_specific"] is True
        assert refreshed[handle].metadata["actionability"] == "NEEDS_PREFLIGHT"
    assert refreshed["founders1"].metadata["registry_refresh_status"] == "VERIFIED"
    cursor = store.get("telegram_registered_native_refresh_cursor", str(product.id))
    assert cursor["last_batch_latest_only"] is False
    assert cursor["last_batch_latest_handles"] == ["founders0", "founders2"]
    assert cursor["last_batch_context_handles"] == ["founders1"]


@pytest.mark.asyncio
async def test_confirmed_peer_failure_does_not_erase_unconfirmed_success():
    store, product = MemoryRuntimeStateStore(), _product()
    for i in range(2):
        _stored(store, product.id, f"founders{i}", 100 + i)
    key = f"{product.id}:entity:101"
    unconfirmed = store.get(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE, key)
    unconfirmed["audience_fit"] = {"confirmed": False}
    unconfirmed["metadata"]["community_fit_pass"] = False
    store.put(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE, key, unconfirmed)

    transport = MixedBatchTransport(failure_limit=1)
    connector = TelegramResearchConnector(transport)
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        research_connector=connector, store=store
    )
    candidates = await adapter.refresh_registered(product, [_icp()])
    results = {c.metadata["handle"]: c.metadata for c in candidates}
    assert results["founders0"]["registry_refresh_status"] == "RETRYABLE_FAILED"
    assert results["founders0"]["registry_refresh_error_type"] == "RuntimeError"
    assert results["founders1"]["registry_refresh_status"] == "VERIFIED"
    assert results["founders1"].get("registry_refresh_error_type") is None
    assert transport.calls == [(("founders0",), 1), (("founders1",), 4)]


@pytest.mark.asyncio
async def test_second_inspection_floodwait_keeps_batch_pending():
    store, product = MemoryRuntimeStateStore(), _product()
    for i in range(2):
        _stored(store, product.id, f"founders{i}", 100 + i)
    key = f"{product.id}:entity:101"
    unconfirmed = store.get(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE, key)
    unconfirmed["audience_fit"] = {"confirmed": False}
    unconfirmed["metadata"]["community_fit_pass"] = False
    store.put(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE, key, unconfirmed)

    transport = MixedBatchTransport(flood_on_limit=4)
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        research_connector=TelegramResearchConnector(transport),
        store=store,
    )
    results = await adapter.refresh_registered(
        product, [_icp()], now=datetime(2026, 10, 19, tzinfo=UTC)
    )
    assert transport.calls == [(("founders0",), 1), (("founders1",), 4)]
    assert all(
        c.metadata["registry_refresh_status"] == "RETRYABLE_FAILED"
        for c in results
    )
    cursor = store.get("telegram_registered_native_refresh_cursor", str(product.id))
    assert cursor["next_index"] == 0
    assert cursor["last_batch_error_type"] == "TELEGRAM_FLOOD_WAIT"


@pytest.mark.asyncio
async def test_connector_latest_only_reads_one_while_unknown_peers_keep_four():
    transport = FakeReadOnlyTransport()
    connector = TelegramResearchConnector(transport)
    await connector.inspect_latest_known(["founders0", "founders1"])
    await connector.inspect_known(["founders2"])
    assert transport.calls == [
        ("inspect_known", ("founders0", "founders1"), 1),
        ("inspect_known", ("founders2",), 4),
    ]
