"""Offline source-aware scheduling tests: respect real native and catalog gates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.autonomous_opportunity_refresh import (
    AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE,
    MIN_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS,
    AutonomousOpportunityRefreshService,
)
from app.autonomy_schemas import GrowthMandateStatus, GrowthMandateView
from app.autonomy_service import GROWTH_MANDATE_NAMESPACE
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_discovery_stage_queue import TelegramDiscoveryStageQueue


def _queue(store, product_id, now, *, registry="SKIPPED_NO_CANDIDATES",
           native="DEFERRED_RATE_LIMIT", catalog="SKIPPED_DISABLED",
           paid="BLOCKED", native_delay_hours=48):
    queue = TelegramDiscoveryStageQueue(
        store, product_id, now=now, run_id="offline-source-retry"
    )
    queue.start()
    queue.mark("registry", registry, reason="NO_REGISTERED_HANDLES")
    queue.mark(
        "native", native, reason="TELEGRAM_FLOOD_WAIT",
        next_attempt_at=(now + timedelta(hours=native_delay_hours)).isoformat(),
    )
    queue.mark("catalog", catalog, reason="OPERATOR_DISABLED")
    queue.mark("paid", paid, reason="PAID_SEARCH_DISABLED")
    return queue


def test_refresh_aligns_to_flood_wait_when_all_sources_blocked():
    store, product_id = MemoryRuntimeStateStore(), uuid4()
    t = datetime(2026, 10, 18, 8, tzinfo=UTC)
    _queue(store, product_id, t)
    service = AutonomousOpportunityRefreshService(store=store)
    result = service._next_source_retry(
        product_id, after=t - timedelta(minutes=1), now=t + timedelta(minutes=1)
    )
    assert result == t + timedelta(hours=48)


def test_refresh_does_not_delay_native_inspection_for_new_catalog_leads():
    store, product_id = MemoryRuntimeStateStore(), uuid4()
    t = datetime(2026, 10, 18, 8, tzinfo=UTC)
    _queue(store, product_id, t, catalog="STAGED_UNVERIFIED")
    service = AutonomousOpportunityRefreshService(store=store)
    assert service._next_source_retry(
        product_id, after=t, now=t + timedelta(minutes=1)
    ) is None


def test_refresh_does_not_delay_if_registered_peers_are_inspectable():
    store, product_id = MemoryRuntimeStateStore(), uuid4()
    t = datetime(2026, 10, 18, 8, tzinfo=UTC)
    _queue(store, product_id, t, registry="COMPLETED")
    service = AutonomousOpportunityRefreshService(store=store)
    assert service._next_source_retry(product_id, after=t, now=t) is None


def test_source_retry_chooses_earliest_allowed_and_minimum_worker_delay():
    store, product_id = MemoryRuntimeStateStore(), uuid4()
    t = datetime(2026, 10, 18, 8, tzinfo=UTC)
    queue = _queue(store, product_id, t, native_delay_hours=48,
                   catalog="DEFERRED_COOLDOWN")
    queue.mark(
        "catalog", "DEFERRED_COOLDOWN", reason="CATALOG_COOLDOWN",
        next_attempt_at=(t + timedelta(hours=5)).isoformat(),
    )
    service = AutonomousOpportunityRefreshService(store=store)
    assert service._next_source_retry(product_id, after=t, now=t) == t + timedelta(hours=5)

    queue.mark(
        "catalog", "DEFERRED_COOLDOWN", reason="CATALOG_COOLDOWN",
        next_attempt_at=(t + timedelta(seconds=5)).isoformat(),
    )
    assert service._next_source_retry(product_id, after=t, now=t) == (
        t + timedelta(seconds=MIN_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS)
    )


def test_previous_run_or_incomplete_stage_queue_does_not_control_new_refresh():
    store, product_id = MemoryRuntimeStateStore(), uuid4()
    t = datetime(2026, 10, 18, 8, tzinfo=UTC)
    _queue(store, product_id, t)
    service = AutonomousOpportunityRefreshService(store=store)
    assert service._next_source_retry(
        product_id, after=t + timedelta(minutes=1), now=t
    ) is None

    # A newly started but crashed run cannot dictate the next scheduling time.
    TelegramDiscoveryStageQueue(
        store, product_id, now=t + timedelta(minutes=2), run_id="crashed"
    ).start()
    assert service._next_source_retry(product_id, after=t, now=t) is None


@pytest.mark.asyncio
async def test_worker_refresh_persists_source_wait_without_any_followup_discovery(
    monkeypatch,
):
    store, product_id = MemoryRuntimeStateStore(), uuid4()
    t = datetime.now(UTC)
    mandate = GrowthMandateView(
        id=uuid4(),
        product_id=product_id,
        version=1,
        status=GrowthMandateStatus.ACTIVE,
        total_budget_cap=100,
        target_max_cac=10,
        max_autonomous_spend_per_experiment=0,
        max_autonomous_spend_per_day=0,
        max_concurrent_running_experiments=1,
        allowed_platforms=[DistributionPlatform.TELEGRAM],
        allowed_actions=[DistributionActionType.COMMENT],
        autonomous_prepare=True,
        autonomous_approve=True,
        autonomous_paid_activation=False,
        approval_threshold=0,
        created_at=t,
        updated_at=t,
    )
    store.put(GROWTH_MANDATE_NAMESPACE, str(product_id), mandate.model_dump(mode="json"))

    class EmptyResearch:
        calls = 0

        async def discover(self, product, icps):
            self.calls += 1
            _queue(store, product.id, datetime.now(UTC))
            return SimpleNamespace(opportunity_count=0)

    audience = EmptyResearch()
    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.product_intake_service.get_product",
        lambda _: SimpleNamespace(id=product_id),
    )
    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.icp_service.get",
        lambda _: SimpleNamespace(product_id=product_id),
    )
    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.distribution_control_plane_service.list_identities",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.distribution_control_plane_service.list_policies",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.distribution_control_plane_service.list_campaign_slots",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.distribution_play_service.generate",
        lambda *args, **kwargs: SimpleNamespace(play_count=0, ready_count=0),
    )
    service = AutonomousOpportunityRefreshService(store=store, audience_service=audience)

    first = await service.run_once(product_id=product_id, interval_seconds=21600)
    assert first[0]["status"] == "REFRESHED"
    assert first[0]["next_attempt_reason"] == "AWAITING_SOURCE_RETRY"
    assert datetime.fromisoformat(first[0]["next_attempt_at"]) > t + timedelta(hours=47)
    again = await service.run_once(product_id=product_id, interval_seconds=21600)
    assert again[0]["status"] == "NOT_DUE"
    assert audience.calls == 1
    saved = store.get(AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE, str(product_id))
    assert saved["consecutive_failures"] == 0
