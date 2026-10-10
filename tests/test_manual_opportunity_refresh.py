from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.autonomous_opportunity_refresh import AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE
from app.manual_opportunity_refresh import ManualOpportunityRefreshService
from app.runtime_store import MemoryRuntimeStateStore


class InspectingRefreshService:
    def __init__(self, store: MemoryRuntimeStateStore) -> None:
        self.store = store
        self.calls: list[dict] = []

    async def run_once(self, *, product_id, interval_seconds):
        marker = self.store.get(
            AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE,
            str(product_id),
        )
        self.calls.append(
            {
                "product_id": product_id,
                "interval_seconds": interval_seconds,
                "marker": marker,
            }
        )
        return [{"product_id": str(product_id), "status": "REFRESHED"}]


class EmptyRefreshService:
    async def run_once(self, *, product_id, interval_seconds):
        return []


class InspectingManualRefreshService(ManualOpportunityRefreshService):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.direct_calls = []

    async def _run_direct_product_discovery(self, product_id):
        self.direct_calls.append(product_id)
        return {
            "product_id": str(product_id),
            "status": "REFRESHED",
            "mode": "MANUAL_DIRECT",
        }


@pytest.mark.asyncio
async def test_force_refresh_clears_only_cadence_marker_before_discovery() -> None:
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    previous = {
        "product_id": str(product_id),
        "status": "REFRESHED",
        "last_success_at": datetime.now(UTC).isoformat(),
        "opportunity_count": 7,
    }
    store.put(
        AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE,
        str(product_id),
        previous,
    )
    delegate = InspectingRefreshService(store)
    service = ManualOpportunityRefreshService(
        store=store,
        refresh_service=delegate,
    )

    result = await service.run_once(product_id=product_id, force=True)

    assert result[0]["status"] == "REFRESHED"
    assert len(delegate.calls) == 1
    marker = delegate.calls[0]["marker"]
    assert marker["last_success_at"] is None
    assert marker["opportunity_count"] == 7
    assert marker["manual_force_requested_at"]


@pytest.mark.asyncio
async def test_non_force_refresh_keeps_existing_cadence_marker() -> None:
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    last_success_at = datetime.now(UTC).isoformat()
    store.put(
        AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE,
        str(product_id),
        {
            "product_id": str(product_id),
            "status": "REFRESHED",
            "last_success_at": last_success_at,
        },
    )
    delegate = InspectingRefreshService(store)
    service = ManualOpportunityRefreshService(
        store=store,
        refresh_service=delegate,
    )

    await service.run_once(product_id=product_id, force=False)

    assert delegate.calls[0]["marker"]["last_success_at"] == last_success_at
    assert "manual_force_requested_at" not in delegate.calls[0]["marker"]


@pytest.mark.asyncio
async def test_manual_refresh_falls_back_to_read_only_direct_discovery_without_mandate() -> None:
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    service = InspectingManualRefreshService(
        store=store,
        refresh_service=EmptyRefreshService(),
    )

    result = await service.run_once(product_id=product_id, force=True)

    assert result == [
        {
            "product_id": str(product_id),
            "status": "REFRESHED",
            "mode": "MANUAL_DIRECT",
        }
    ]
    assert service.direct_calls == [product_id]
