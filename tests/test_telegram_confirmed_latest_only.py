"""Known, audience-qualified communities need only one post per inspection.

Unqualified audiences retain the existing limited sample so their community
fit can be established. Never search for new groups or send messages here.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.community_first_telegram_adapter import CommunityFirstTelegramDiscoveryAdapter
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_community_registry import TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE
from app.telegram_research import (
    TelegramKnownCommunityInspection,
    TelegramResearchConnector,
)


class KnownPeerTransport:
    def __init__(self):
        self.calls = []
        self.discover_calls = 0

    async def inspect_known(self, handles, *, recent_message_limit):
        self.calls.append((tuple(handles), recent_message_limit))
        return [
            TelegramKnownCommunityInspection(handle=h, status="RETRYABLE_FAILED")
            for h in handles
        ]

    async def discover_public(self, *args, **kwargs):
        self.discover_calls += 1
        raise AssertionError("Known-peer inspection must not run global search")


def _stored(store, product_id, handle: str, *, confirmed: bool):
    record_id = f"{product_id}:entity:{handle}"
    store.put(
        TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE, record_id,
        {
            "id": record_id,
            "product_id": str(product_id),
            "canonical_key": f"telegram:{handle}:community",
            "title": handle,
            "url": f"https://t.me/{handle}",
            "kind": "GROUP",
            "audience_fit": {"confirmed": confirmed},
            "native_snapshot": {"handle": handle},
            "metadata": {"handle": handle, "access_state": "PUBLIC"},
        },
    )


def _icp():
    return SimpleNamespace(
        title="Founders", pain="Find peers", trigger="Looking for peers",
        alternatives=[],
    )


@pytest.mark.asyncio
async def test_confirmed_batch_reads_only_latest_message_not_four():
    store, product_id, transport = MemoryRuntimeStateStore(), uuid4(), KnownPeerTransport()
    for i in range(3):
        _stored(store, product_id, f"founders{i}", confirmed=True)
    connector = TelegramResearchConnector(
        transport, known_handle_limit=3, recent_message_limit=4
    )
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        research_connector=connector, store=store
    )
    product = SimpleNamespace(id=product_id, market="US", language="English")
    await adapter.refresh_registered(product, [_icp()])
    assert transport.calls == [(("founders0", "founders1", "founders2"), 1)]
    cursor = store.get("telegram_registered_native_refresh_cursor", str(product_id))
    assert cursor["last_batch_latest_only"] is True
    assert transport.discover_calls == 0


@pytest.mark.asyncio
async def test_new_group_with_unconfirmed_audience_keeps_short_fit_sample():
    store, product_id, transport = MemoryRuntimeStateStore(), uuid4(), KnownPeerTransport()
    for i, confirmed in enumerate((True, False, True)):
        _stored(store, product_id, f"founders{i}", confirmed=confirmed)
    connector = TelegramResearchConnector(
        transport, known_handle_limit=3, recent_message_limit=4
    )
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        research_connector=connector, store=store
    )
    product = SimpleNamespace(id=product_id, market="US", language="English")
    await adapter.refresh_registered(product, [_icp()])
    assert transport.calls == [
        (("founders0", "founders2"), 1),
        (("founders1",), 4),
    ]
    cursor = store.get("telegram_registered_native_refresh_cursor", str(product_id))
    assert cursor["last_batch_latest_only"] is False
    assert transport.discover_calls == 0


@pytest.mark.asyncio
async def test_standalone_connector_latest_and_normal_modes_share_same_safety_cap():
    transport = KnownPeerTransport()
    connector = TelegramResearchConnector(
        transport, known_handle_limit=2, recent_message_limit=4
    )
    await connector.inspect_latest_known(["@Founders0", "founders0", "founders1", "founders2"])
    await connector.inspect_known(["Founders0", "founders1", "founders2"])
    assert transport.calls == [
        (("founders0", "founders1"), 1),
        (("founders0", "founders1"), 4),
    ]
    assert transport.discover_calls == 0
