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


@pytest.mark.asyncio
async def test_join_required_reaches_planner_only_when_owner_enabled_auto_join():
    from app.distribution_play_planner import DistributionPlayPlanner
    from app.distribution_types import DistributionActionType

    store = MemoryRuntimeStateStore()
    project, product = uuid4(), uuid4()
    store.put("customer_acquisition_projects", str(project), {
        "id": str(project), "product_id": str(product),
        "channel_preferences": {"TELEGRAM": "AUTO"},
        "channel_publisher_modes": {"TELEGRAM": "CLIENT_OWNED"},
    })
    store.put("customer_telegram_automation_authorization", str(project), {
        "project_id": str(project), "status": "ENABLED",
        "allow_community_joins": True,
    })
    now = datetime.now(UTC)
    action_url = "https://t.me/community/406"
    metadata = {
        "handle": "community", "telegram_entity_id": 1234,
        "native_research_status": "VERIFIED",
        "community_fit_pass": True,
        "research_audience_status": "DIRECT_CONFIRMED",
        "action_target_mode": "CHANNEL_COMMENT",
        "action_target_url": action_url,
        "action_target_specific": True,
        "source_checked_at": now.isoformat(),
        "surface_capabilities": {"comment": "AVAILABLE", "publisher_permission_verified": False},
        "community_commercial_rules": {
            "status": "VERIFIED_ALLOWED", "allows_participation": True,
            "allows_promotion": True, "source_url": "https://t.me/community",
        },
    }
    opp = DistributionOpportunityView(
        id=uuid4(), icp_id=uuid4(), title="Community",
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.CHANNEL,
        canonical_key="telegram:1234:community",
        url="https://t.me/community", metadata=metadata,
    )
    preflight = TelegramOpportunityPreflightResult(
        status="JOIN_REQUIRED", handle="community",
        post_id=406, linked_chat_id=777,
        action_target_mode="CHANNEL_COMMENT",
    )
    service = AutonomousOpportunityRefreshService(
        store=store, audience_service=SimpleNamespace(),
        telegram_preflight_service=ModeAwarePreflight(),
    )
    distribution = AudienceDistributionMapView(
        product_id=product, top_icp_count=1, opportunity_count=1,
        opportunities=[opp],
    )
    key = service._preflight_key(action_url, "CHANNEL_COMMENT")
    result = await service._apply_comment_preflight_to_distribution(
        project, distribution, {key: preflight},
    )
    updated = result.opportunities[0]
    assert updated.metadata["publisher_participation_preflight"]["status"] == "JOIN_REQUIRED"
    assert updated.metadata["surface_capabilities"]["comment"] == "UNAVAILABLE"
    assert updated.metadata["autonomous_join_recoverable"] is True
    planner = DistributionPlayPlanner()
    identity = SimpleNamespace(profile_config={"publisher_mode": "CLIENT_OWNED"})
    assert planner._telegram_native_execution_reasons(
        updated, DistributionActionType.COMMENT, identity=identity
    ) == []

    # The same opportunity remains unsendable when automation is disabled.
    auth = store.get("customer_telegram_automation_authorization", str(project))
    auth["allow_community_joins"] = False
    store.put("customer_telegram_automation_authorization", str(project), auth)
    denied = await service._apply_comment_preflight_to_distribution(
        project, distribution, {key: preflight},
    )
    assert denied.opportunities[0].metadata["autonomous_join_recoverable"] is False
    assert planner._telegram_native_execution_reasons(
        denied.opportunities[0], DistributionActionType.COMMENT, identity=identity
    ) != []


@pytest.mark.asyncio
async def test_unknown_rules_block_joinrequired_planning_even_under_automation():
    from app.distribution_play_planner import DistributionPlayPlanner
    from app.distribution_types import DistributionActionType

    store = MemoryRuntimeStateStore()
    project = uuid4()
    store.put("customer_acquisition_projects", str(project), {
        "id": str(project), "channel_preferences": {"TELEGRAM": "AUTO"},
        "channel_publisher_modes": {"TELEGRAM": "CLIENT_OWNED"},
    })
    store.put("customer_telegram_automation_authorization", str(project), {
        "status": "ENABLED", "allow_community_joins": True,
    })
    service = AutonomousOpportunityRefreshService(store=store)
    metadata = {
        "native_research_status": "VERIFIED", "community_fit_pass": True,
        "research_audience_status": "DIRECT_CONFIRMED",
        "action_target_mode": "STANDALONE_POST",
        "action_target_url": "https://t.me/group",
        "community_commercial_rules": {"status": "UNVERIFIED"},
    }
    assert service._join_recovery_candidacy(
        project, metadata, mode="STANDALONE_POST",
        native_surface="AVAILABLE", status="JOIN_REQUIRED",
    ) is False
    # A malicious raw marker without the associated provider JOIN_REQUIRED
    # status must not satisfy the execution planner.
    opp = DistributionOpportunityView(
        id=uuid4(), icp_id=uuid4(), title="Group",
        platform=DistributionPlatform.TELEGRAM, kind=OpportunityKind.GROUP,
        canonical_key="telegram:1234:community", url="https://t.me/group",
        metadata={**metadata, "autonomous_join_recoverable": True,
                  "publisher_participation_preflight": {"status": "NO_WRITE_ACCESS"},
                  "surface_capabilities": {"standalone_post": "UNAVAILABLE"}},
    )
    reasons = DistributionPlayPlanner()._telegram_native_execution_reasons(
        opp, DistributionActionType.STANDALONE_POST,
        identity=SimpleNamespace(profile_config={"publisher_mode": "CLIENT_OWNED"}),
    )
    assert reasons
