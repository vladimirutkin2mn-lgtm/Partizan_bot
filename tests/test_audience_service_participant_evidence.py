from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.audience_intelligence_service as audience_service_module
from app.audience_intelligence_service import InMemoryAudienceIntelligenceService
from app.distribution_schemas import DistributionOpportunitySeed
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.runtime_store import MemoryRuntimeStateStore


class FakeEngine:
    last_attempts = []

    def __init__(self, opportunity):
        self.opportunity = opportunity

    async def discover(self, product, icps):
        del product, icps
        return [self.opportunity]


class FakeParticipantResearch:
    async def enrich(self, opportunities, icps):
        del icps
        result = []
        for opportunity in opportunities:
            metadata = dict(opportunity.metadata)
            metadata["participant_evidence"] = {
                "status": "OBSERVED",
                "matched_participant_count": 3,
                "matched_message_count": 4,
                "repeat_participant_count": 1,
                "identity_persisted": False,
                "participant_ids_persisted": False,
                "usernames_persisted": False,
                "execution_permission_inferred": False,
            }
            result.append(opportunity.model_copy(update={"metadata": metadata}))
        return result


def _opportunity(icp_id):
    return DistributionOpportunitySeed(
        icp_id=icp_id,
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key="telegram:321:community",
        title="Career community",
        url="https://t.me/careercommunity",
        relevance_score=70,
        rationale="career audience evidence",
        metadata={
            "handle": "careercommunity",
            "surface_parent_key": "telegram:321",
            "opportunity_scope": "COMMUNITY",
            "actionability": "RESEARCH_ONLY",
            "community_only_reason": "NO_FRESH_ACTION_TARGET",
            "intelligence_scores": {
                "version": "v2",
                "audience_fit": 60.0,
                "context_fit": 35.0,
                "actionability": 20.0,
                "confidence": 50.0,
                "ranking_score": 45.5,
                "exploration_candidate": False,
                "execution_permission_inferred": False,
            },
        },
        evidence=[],
    )


@pytest.mark.asyncio
async def test_service_enriches_before_persisted_intelligence_recompute(monkeypatch) -> None:
    monkeypatch.setattr(
        audience_service_module,
        "telegram_participant_research_service",
        FakeParticipantResearch(),
    )
    monkeypatch.setattr(
        audience_service_module,
        "customer_live_opportunity_service",
        SimpleNamespace(sync_distribution_opportunities=lambda *_args, **_kwargs: None),
    )
    store = MemoryRuntimeStateStore()
    service = InMemoryAudienceIntelligenceService(store)
    product = SimpleNamespace(id=uuid4())
    icp_id = uuid4()
    icp = SimpleNamespace(id=icp_id)
    result = await service._discover_with_engine(
        product,
        SimpleNamespace(icps=[icp]),
        engine=FakeEngine(_opportunity(icp_id)),
        top_icp_count=1,
        merge_existing=False,
    )

    opportunity = result.opportunities[0]
    scores = opportunity.metadata["intelligence_scores"]
    participant_signal = opportunity.metadata["participant_signal"]

    assert opportunity.metadata["participant_evidence"]["identity_persisted"] is False
    assert scores["confidence"] == 56.5
    assert scores["audience_fit"] == 60.0
    assert scores["context_fit"] == 35.0
    assert scores["actionability"] == 20.0
    assert participant_signal["confidence_bonus"] == 6.5
    assert participant_signal["actionability_propagated"] is False
    assert participant_signal["execution_permission_inferred"] is False

    persisted = service.get(product.id).opportunities[0]
    assert persisted.metadata["intelligence_scores"]["confidence"] == 56.5
