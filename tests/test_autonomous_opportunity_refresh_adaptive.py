from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.autonomous_opportunity_refresh import (
    DISTRIBUTION_LEARNING_NAMESPACE,
    AutonomousOpportunityRefreshService,
)
from app.autonomy_schemas import GrowthMandateStatus, GrowthMandateView
from app.autonomy_service import GROWTH_MANDATE_NAMESPACE
from app.customer_live_opportunities import CUSTOMER_PROJECT_NAMESPACE
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_opportunity_preflight import TelegramOpportunityPreflightResult


class AdaptiveAudienceService:
    def __init__(self) -> None:
        self.discover_calls = 0
        self.adaptive_calls: list[list[str]] = []
        self.opportunities: dict[str, object] = {}

    async def discover(self, product, icps):
        del product, icps
        self.discover_calls += 1
        return SimpleNamespace(opportunity_count=2, opportunities=[])

    async def discover_with_hints(
        self,
        product,
        icps,
        *,
        telegram_hints,
        merge_existing=True,
    ):
        del product, icps, merge_existing
        self.adaptive_calls.append(list(telegram_hints))
        return SimpleNamespace(
            opportunity_count=2 + len(self.adaptive_calls) * 3,
            opportunities=[],
        )

    def find_opportunity(self, opportunity_id):
        key = str(opportunity_id)
        if key not in self.opportunities:
            raise KeyError(key)
        return self.opportunities[key]


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


def _icps(product_id):
    return SimpleNamespace(
        product_id=product_id,
        icps=[
            SimpleNamespace(
                title="People exploring a niche",
                description="People who want trusted guidance and peer experience",
                pain="hard to know where to start safely",
                desired_outcome="find a useful and welcoming community",
                trigger="ready to ask questions",
                alternatives=["peer education", "topic meetups", "expert advice"],
            )
        ],
    )


def _product(product_id):
    return SimpleNamespace(
        id=product_id,
        name="Example",
        problem_or_desire="help people find trusted guidance",
        value_proposition="useful guidance from relevant communities",
        known_audience=["first-time users", "curious learners"],
    )


@pytest.mark.asyncio
async def test_refresh_keeps_searching_until_ready_target_is_reached(monkeypatch) -> None:
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
        {"id": str(project_id), "product_id": str(product_id)},
    )
    audience = AdaptiveAudienceService()
    service = AutonomousOpportunityRefreshService(store=store, audience_service=audience)

    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.product_intake_service.get_product",
        lambda _: _product(product_id),
    )
    monkeypatch.setattr(
        "app.autonomous_opportunity_refresh.icp_service.get",
        lambda _: _icps(product_id),
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
        lambda *args, **kwargs: SimpleNamespace(play_count=8, ready_count=5),
    )

    preflight_rounds = [
        {
            "https://t.me/a/1": TelegramOpportunityPreflightResult(status="READY"),
            "https://t.me/b/1": TelegramOpportunityPreflightResult(status="NO_DISCUSSION"),
        },
        {
            "https://t.me/c/1": TelegramOpportunityPreflightResult(status="READY"),
            "https://t.me/d/1": TelegramOpportunityPreflightResult(status="READY"),
        },
        {
            "https://t.me/e/1": TelegramOpportunityPreflightResult(status="READY"),
            "https://t.me/f/1": TelegramOpportunityPreflightResult(status="READY"),
        },
    ]

    async def fake_preflight(_project_id):
        return preflight_rounds.pop(0)

    monkeypatch.setattr(service, "_preflight_live_comment_targets", fake_preflight)

    result = await service.run_once(interval_seconds=21600)

    assert result[0]["status"] == "REFRESHED"
    assert result[0]["telegram_preflight_ready"] == 5
    assert result[0]["adaptive_discovery_rounds"] == 3
    assert result[0]["adaptive_stop_reason"] == "TARGET_READY_REACHED"
    assert result[0]["adaptive_hypothesis_count"] > 0
    assert audience.discover_calls == 1
    assert len(audience.adaptive_calls) == 2
    assert audience.adaptive_calls[0] != audience.adaptive_calls[1]


def test_positive_growth_learning_becomes_next_discovery_hypothesis() -> None:
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    opportunity_id = uuid4()
    audience = AdaptiveAudienceService()
    audience.opportunities[str(opportunity_id)] = SimpleNamespace(
        title="Trusted peer education",
        metadata={
            "research_signals": {
                "matched_terms": ["education", "beginners", "questions"],
            },
            "recent_context": [
                {"matched_terms": ["consent", "boundaries", "advice"]},
            ],
        },
    )
    store.put(
        DISTRIBUTION_LEARNING_NAMESPACE,
        str(uuid4()),
        {
            "product_id": str(product_id),
            "platform": "TELEGRAM",
            "opportunity_id": str(opportunity_id),
            "action": "SCALE",
            "paid_users": 3,
            "replies": 4,
            "removals": 0,
            "created_at": datetime.now(UTC).isoformat(),
        },
    )
    service = AutonomousOpportunityRefreshService(store=store, audience_service=audience)

    hints = service._learning_hints(product_id)

    joined = " | ".join(hints).lower()
    assert "education beginners questions" in joined
    assert "consent boundaries advice" in joined
    assert "trusted peer education" in joined
