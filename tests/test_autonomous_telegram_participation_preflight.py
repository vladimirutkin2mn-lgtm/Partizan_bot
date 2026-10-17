from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.autonomous_opportunity_refresh import AutonomousOpportunityRefreshService
from app.customer_live_opportunities import (
    CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE,
    CustomerLiveOpportunityService,
    CustomerLiveOpportunityUpsert,
)
from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_opportunity_preflight import TelegramOpportunityPreflightResult


class ModeAwarePreflight:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def check_target(self, project_id, target_url, mode):
        del project_id
        self.calls.append((str(target_url), str(mode)))
        return TelegramOpportunityPreflightResult(
            status="READY",
            handle="community",
            action_target_mode=str(mode),
        )


@pytest.mark.asyncio
async def test_autonomous_live_preflight_counts_all_three_telegram_action_modes(
    monkeypatch,
) -> None:
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    live_service = CustomerLiveOpportunityService(store)
    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.customer_live_opportunity_service",
        live_service,
    )
    current = datetime.now(UTC)
    for kind, url in (
        ("COMMENT", "https://t.me/channel/10"),
        ("REPLY", "https://t.me/group/20"),
        ("STANDALONE_POST", "https://t.me/group"),
    ):
        live_service.upsert(
            project_id,
            CustomerLiveOpportunityUpsert(
                platform="TELEGRAM",
                kind=kind,
                title=kind,
                url=url,
                rationale="Relevant",
                recommended_action="Preflight",
                publishability="NEEDS_REVIEW",
                discovered_at=current,
                expires_at=current + timedelta(days=7),
                last_checked_at=current,
            ),
        )

    preflight = ModeAwarePreflight()
    service = AutonomousOpportunityRefreshService(
        store=store,
        audience_service=SimpleNamespace(),
        telegram_preflight_service=preflight,
    )

    results = await service._preflight_live_comment_targets(project_id)

    assert len(results) == 3
    assert {mode for _, mode in preflight.calls} == {
        "CHANNEL_COMMENT",
        "GROUP_REPLY",
        "STANDALONE_POST",
    }
    assert service._ready_count(results) == 3
    rows = store.list_namespace(CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE)
    assert {row["publishability"] for row in rows} == {"READY"}


@pytest.mark.asyncio
async def test_distribution_preflight_updates_only_the_capability_for_each_action_mode() -> None:
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    product_id = uuid4()
    reply_id = uuid4()
    post_id = uuid4()

    reply = DistributionOpportunityView(
        id=reply_id,
        icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key="telegram:42:community",
        title="Reply group",
        url="https://t.me/group",
        relevance_score=90,
        rationale="Relevant",
        metadata={
            "handle": "group",
            "action_target_url": "https://t.me/group/20",
            "action_target_specific": True,
            "action_target_mode": "GROUP_REPLY",
            "surface_capabilities": {
                "comment": "UNAVAILABLE",
                "reply": "AVAILABLE",
                "standalone_post": "AVAILABLE",
            },
        },
    )
    standalone = DistributionOpportunityView(
        id=post_id,
        icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key="telegram:43:community",
        title="Post group",
        url="https://t.me/group2",
        relevance_score=89,
        rationale="Relevant",
        metadata={
            "handle": "group2",
            "action_target_url": "https://t.me/group2",
            "action_target_specific": False,
            "action_target_mode": "STANDALONE_POST",
            "surface_capabilities": {
                "comment": "UNAVAILABLE",
                "reply": "UNAVAILABLE",
                "standalone_post": "AVAILABLE",
            },
        },
    )
    distribution = AudienceDistributionMapView(
        product_id=product_id,
        top_icp_count=1,
        opportunity_count=2,
        opportunities=[reply, standalone],
        diagnostics={},
    )
    preflight = ModeAwarePreflight()
    audience = SimpleNamespace(update_opportunity=lambda item: None)
    service = AutonomousOpportunityRefreshService(
        store=store,
        audience_service=audience,
        telegram_preflight_service=preflight,
    )

    refreshed = await service._apply_comment_preflight_to_distribution(
        project_id,
        distribution,
        {},
    )

    by_id = {item.id: item for item in refreshed.opportunities}
    reply_metadata = by_id[reply_id].metadata
    post_metadata = by_id[post_id].metadata
    assert reply_metadata["surface_capabilities"]["reply"] == "AVAILABLE"
    assert reply_metadata["publisher_participation_preflight"]["status"] == "READY"
    assert reply_metadata["publisher_participation_preflight"]["action_target_mode"] == (
        "GROUP_REPLY"
    )
    assert post_metadata["surface_capabilities"]["standalone_post"] == "AVAILABLE"
    assert post_metadata["publisher_participation_preflight"]["status"] == "READY"
    assert post_metadata["publisher_participation_preflight"]["action_target_mode"] == (
        "STANDALONE_POST"
    )
