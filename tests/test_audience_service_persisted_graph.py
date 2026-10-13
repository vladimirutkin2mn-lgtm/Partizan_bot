from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.audience_intelligence_service as audience_service_module
from app.audience_intelligence_service import InMemoryAudienceIntelligenceService
from app.distribution_schemas import DistributionOpportunitySeed
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.runtime_store import MemoryRuntimeStateStore


class FakeEngine:
    def __init__(self, opportunities):
        self._opportunities = opportunities
        self.last_attempts = []

    async def discover(self, product, icps):
        del product, icps
        return list(self._opportunities)


def _scores(audience, confidence, actionability):
    return {
        "version": "v2",
        "audience_fit": audience,
        "context_fit": 25.0,
        "actionability": actionability,
        "confidence": confidence,
        "ranking_score": 40.0,
        "exploration_candidate": False,
        "execution_permission_inferred": False,
    }


def _community(icp_id):
    now = datetime.now(UTC)
    return DistributionOpportunitySeed(
        icp_id=icp_id,
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key="telegram:900:community",
        title="Community",
        url="https://t.me/example",
        relevance_score=45,
        rationale="community",
        metadata={
            "surface_parent_key": "telegram:900",
            "opportunity_scope": "COMMUNITY",
            "actionability": "RESEARCH_ONLY",
            "community_only_reason": "NO_FRESH_ACTION_TARGET",
            "source_checked_at": now.isoformat(),
            "intelligence_scores": _scores(48.0, 50.0, 20.0),
        },
        evidence=[],
    )


def _post(icp_id, message_id):
    now = datetime.now(UTC)
    target = f"https://t.me/example/{message_id}"
    return DistributionOpportunitySeed(
        icp_id=icp_id,
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key=f"telegram:900:post:{message_id}",
        title=f"Post {message_id}",
        url="https://t.me/example",
        relevance_score=80,
        rationale="post",
        metadata={
            "surface_parent_key": "telegram:900",
            "opportunity_scope": "POST",
            "actionability": "NEEDS_PREFLIGHT",
            "action_target_url": target,
            "action_target_specific": True,
            "recent_context": [
                {
                    "url": target,
                    "published_at": (now - timedelta(hours=1)).isoformat(),
                    "text": "recent audience evidence",
                }
            ],
            "intelligence_scores": _scores(80.0, 75.0, 70.0),
        },
        evidence=[],
    )


@pytest.mark.asyncio
async def test_service_recomputes_graph_after_adaptive_merge(monkeypatch) -> None:
    monkeypatch.setattr(
        audience_service_module,
        "customer_live_opportunity_service",
        SimpleNamespace(sync_distribution_opportunities=lambda *_args, **_kwargs: None),
    )
    store = MemoryRuntimeStateStore()
    service = InMemoryAudienceIntelligenceService(store)
    product = SimpleNamespace(id=uuid4())
    icp_id = uuid4()
    icp_result = SimpleNamespace(icps=[SimpleNamespace(id=icp_id)])

    first = await service._discover_with_engine(
        product,
        icp_result,
        engine=FakeEngine([_community(icp_id)]),
        top_icp_count=1,
        merge_existing=False,
    )
    first_community = next(
        item for item in first.opportunities if item.canonical_key.endswith(":community")
    )
    assert first_community.metadata["intelligence_scores"]["audience_fit"] == 48.0

    second = await service._discover_with_engine(
        product,
        icp_result,
        engine=FakeEngine([_post(icp_id, 1), _post(icp_id, 2)]),
        top_icp_count=1,
        merge_existing=True,
    )
    second_community = next(
        item for item in second.opportunities if item.canonical_key.endswith(":community")
    )

    assert second.opportunity_count == 3
    assert second_community.metadata["intelligence_scores"]["audience_fit"] > 48.0
    assert second_community.metadata["intelligence_scores"]["confidence"] > 50.0
    assert second_community.metadata["graph_signal_propagation"]["direction"] == "POSTS_TO_COMMUNITY"

    persisted = service.get(product.id)
    persisted_community = next(
        item for item in persisted.opportunities if item.canonical_key.endswith(":community")
    )
    assert persisted_community.metadata["intelligence_scores"]["audience_fit"] == (
        second_community.metadata["intelligence_scores"]["audience_fit"]
    )
