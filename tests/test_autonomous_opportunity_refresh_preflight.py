from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.autonomous_opportunity_refresh import AutonomousOpportunityRefreshService
from app.autonomy_schemas import GrowthMandateStatus, GrowthMandateView
from app.autonomy_service import GROWTH_MANDATE_NAMESPACE
from app.customer_live_opportunities import (
    CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE,
    CUSTOMER_PROJECT_NAMESPACE,
    CustomerLiveOpportunityService,
    CustomerLiveOpportunityUpsert,
)
from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionActionType, DistributionPlatform, OpportunityKind
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_opportunity_preflight import TelegramOpportunityPreflightResult


class FakeAudienceService:
    def __init__(self, distribution: AudienceDistributionMapView) -> None:
        self.distribution = distribution

    async def discover(self, product, icps):
        del product, icps
        return self.distribution

    def update_opportunity(self, updated: DistributionOpportunityView) -> None:
        self.distribution = self.distribution.model_copy(
            update={
                "opportunities": [
                    updated if item.id == updated.id else item
                    for item in self.distribution.opportunities
                ]
            }
        )


class NoDiscussionPreflight:
    async def check_comment_target(self, project_id, target_url):
        del project_id, target_url
        return TelegramOpportunityPreflightResult(
            status="NO_DISCUSSION",
            handle="Bizarre_afisha",
            post_id=794,
            reason="TARGET_HAS_NO_DISCUSSION",
        )


def _mandate(product_id) -> GrowthMandateView:
    now = datetime.now(UTC)
    return GrowthMandateView(
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
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_refresh_marks_no_discussion_stale_and_removes_comment_execution_capability(
    monkeypatch,
) -> None:
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    project_id = uuid4()
    mandate = _mandate(product_id)
    store.put(
        GROWTH_MANDATE_NAMESPACE,
        str(product_id),
        mandate.model_dump(mode="json"),
    )
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {
            "id": str(project_id),
            "product_id": str(product_id),
            "research_state": "READY",
            "deleted_at": None,
        },
    )

    target_url = "https://t.me/Bizarre_afisha/794"
    live_service = CustomerLiveOpportunityService(store)
    live_service.upsert(
        project_id,
        CustomerLiveOpportunityUpsert(
            platform="TELEGRAM",
            kind="COMMENT",
            title="Bizarre 794",
            url=target_url,
            rationale="Candidate",
            recommended_action="Review",
            publishability="NEEDS_REVIEW",
            discovered_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=3),
        ),
    )
    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.customer_live_opportunity_service",
        live_service,
    )

    opportunity = DistributionOpportunityView(
        id=uuid4(),
        icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.CHANNEL,
        canonical_key="telegram:123",
        title="Bizarre",
        url="https://t.me/Bizarre_afisha",
        relevance_score=96,
        rationale="Strong fit",
        metadata={
            "handle": "Bizarre_afisha",
            "native_research_status": "VERIFIED",
            "action_target_url": target_url,
            "action_target_specific": True,
            "surface_capabilities": {
                "comment": "AVAILABLE",
                "reply": "UNAVAILABLE",
                "standalone_post": "UNAVAILABLE",
                "publisher_permission_verified": False,
            },
        },
    )
    distribution = AudienceDistributionMapView(
        product_id=product_id,
        top_icp_count=1,
        opportunity_count=1,
        opportunities=[opportunity],
        diagnostics={},
    )
    audience = FakeAudienceService(distribution)
    captured = {}

    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.product_intake_service.get_product",
        lambda candidate_product_id: SimpleNamespace(id=candidate_product_id),
    )
    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.icp_service.get",
        lambda candidate_product_id: SimpleNamespace(product_id=candidate_product_id),
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

    def generate(product, refreshed_distribution, **kwargs):
        del product, kwargs
        captured["distribution"] = refreshed_distribution
        return SimpleNamespace(play_count=1, ready_count=0)

    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.distribution_play_service.generate",
        generate,
    )

    service = AutonomousOpportunityRefreshService(
        store=store,
        audience_service=audience,
        telegram_preflight_service=NoDiscussionPreflight(),
    )
    result = await service.run_once(product_id=product_id, interval_seconds=21600)

    assert result[0]["status"] == "REFRESHED"
    assert result[0]["telegram_preflight_checked"] == 1
    assert result[0]["telegram_preflight_excluded"] == 1

    rows = store.list_namespace(CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE)
    assert len(rows) == 1
    assert rows[0]["status"] == "STALE"
    assert rows[0]["publishability"] == "NO_DISCUSSION"

    refreshed = captured["distribution"].opportunities[0]
    assert refreshed.metadata["publisher_comment_preflight"]["status"] == "NO_DISCUSSION"
    assert refreshed.metadata["surface_capabilities"]["comment"] == "UNAVAILABLE"
