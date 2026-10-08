from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.autonomous_opportunity_refresh import (
    AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE,
    AutonomousOpportunityRefreshService,
)
from app.autonomy_schemas import GrowthMandateStatus, GrowthMandateView
from app.autonomy_service import GROWTH_MANDATE_NAMESPACE
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore


class FakeAudienceService:
    def __init__(self) -> None:
        self.calls = 0

    async def discover(self, product, icps):
        del product, icps
        self.calls += 1
        return SimpleNamespace(opportunity_count=3)


def _mandate(*, status: GrowthMandateStatus = GrowthMandateStatus.ACTIVE) -> GrowthMandateView:
    now = datetime.now(UTC)
    return GrowthMandateView(
        id=uuid4(),
        product_id=uuid4(),
        version=1,
        status=status,
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
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_refresh_discovers_and_rebuilds_plays_only_when_due(monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    mandate = _mandate()
    store.put(
        GROWTH_MANDATE_NAMESPACE,
        str(mandate.product_id),
        mandate.model_dump(mode="json"),
    )
    audience = FakeAudienceService()
    service = AutonomousOpportunityRefreshService(store=store, audience_service=audience)

    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.product_intake_service.get_product",
        lambda product_id: SimpleNamespace(id=product_id),
    )
    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.icp_service.get",
        lambda product_id: SimpleNamespace(product_id=product_id),
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
        lambda *args, **kwargs: SimpleNamespace(play_count=5, ready_count=2),
    )

    first = await service.run_once(interval_seconds=21600)
    second = await service.run_once(interval_seconds=21600)

    assert first[0]["status"] == "REFRESHED"
    assert first[0]["opportunity_count"] == 3
    assert first[0]["play_count"] == 5
    assert first[0]["ready_play_count"] == 2
    assert second[0]["status"] == "NOT_DUE"
    assert audience.calls == 1
    marker = store.get(AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE, str(mandate.product_id))
    assert marker is not None
    assert marker["status"] == "REFRESHED"
    assert marker["last_success_at"]


@pytest.mark.asyncio
async def test_refresh_ignores_paused_and_non_telegram_mandates() -> None:
    store = MemoryRuntimeStateStore()
    paused = _mandate(status=GrowthMandateStatus.PAUSED)
    store.put(
        GROWTH_MANDATE_NAMESPACE,
        str(paused.product_id),
        paused.model_dump(mode="json"),
    )
    now = datetime.now(UTC)
    non_telegram = paused.model_copy(
        update={
            "id": uuid4(),
            "product_id": uuid4(),
            "status": GrowthMandateStatus.ACTIVE,
            "allowed_platforms": [DistributionPlatform.INSTAGRAM],
            "allowed_actions": [DistributionActionType.PAID_CAMPAIGN],
            "created_at": now,
            "updated_at": now,
        }
    )
    store.put(
        GROWTH_MANDATE_NAMESPACE,
        str(non_telegram.product_id),
        non_telegram.model_dump(mode="json"),
    )
    audience = FakeAudienceService()
    service = AutonomousOpportunityRefreshService(store=store, audience_service=audience)

    result = await service.run_once(interval_seconds=21600)

    assert result == []
    assert audience.calls == 0


@pytest.mark.asyncio
async def test_failed_refresh_preserves_retryability_and_safe_diagnostic(monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    mandate = _mandate()
    store.put(
        GROWTH_MANDATE_NAMESPACE,
        str(mandate.product_id),
        mandate.model_dump(mode="json"),
    )

    class FailingAudienceService:
        async def discover(self, product, icps):
            del product, icps
            raise RuntimeError("provider detail that should not be persisted")

    service = AutonomousOpportunityRefreshService(
        store=store,
        audience_service=FailingAudienceService(),
    )
    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.product_intake_service.get_product",
        lambda product_id: SimpleNamespace(id=product_id),
    )
    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.icp_service.get",
        lambda product_id: SimpleNamespace(product_id=product_id),
    )

    first = await service.run_once(interval_seconds=21600)
    second = await service.run_once(interval_seconds=21600)

    assert first[0]["status"] == "FAILED"
    assert first[0]["error_type"] == "RuntimeError"
    assert "provider detail" not in str(first[0])
    assert second[0]["status"] == "FAILED"
    assert first[0]["last_success_at"] is None
