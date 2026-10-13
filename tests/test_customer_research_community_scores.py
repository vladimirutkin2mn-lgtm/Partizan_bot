from uuid import uuid4

from app.customer_research_communities import (
    AUDIENCE_MAP_NAMESPACE,
    CUSTOMER_PROJECT_NAMESPACE,
    CustomerResearchCommunityService,
)
from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.runtime_store import MemoryRuntimeStateStore


def test_research_view_exposes_explicit_intelligence_dimensions_without_publish_fields() -> None:
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    product_id = uuid4()
    opportunity = DistributionOpportunityView(
        id=uuid4(),
        icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key="telegram:adjacent_audience",
        title="Adjacent audience",
        url="https://t.me/adjacent_audience/777",
        relevance_score=73.0,
        rationale="Strong audience evidence in a less obvious context.",
        metadata={
            "opportunity_scope": "COMMUNITY",
            "actionability": "RESEARCH_ONLY",
            "handle": "adjacent_audience",
            "intelligence_scores": {
                "audience_fit": 82.0,
                "context_fit": 31.0,
                "actionability": 15.0,
                "confidence": 70.0,
                "ranking_score": 61.1,
                "exploration_candidate": True,
                "execution_permission_inferred": False,
            },
        },
    )
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {"id": str(project_id), "product_id": str(product_id)},
    )
    audience_map = AudienceDistributionMapView(
        product_id=product_id,
        top_icp_count=1,
        opportunity_count=1,
        opportunities=[opportunity],
    )
    store.put(
        AUDIENCE_MAP_NAMESPACE,
        str(product_id),
        audience_map.model_dump(mode="json"),
    )

    item = CustomerResearchCommunityService(store).list_for_project(project_id)[0]
    payload = item.model_dump(mode="json")

    assert item.audience_fit == 82.0
    assert item.context_fit == 31.0
    assert item.actionability_score == 15.0
    assert item.confidence_score == 70.0
    assert item.ranking_score == 61.1
    assert item.exploration_candidate is True
    assert str(item.community_url).rstrip("/") == "https://t.me/adjacent_audience"
    assert "action_target_url" not in payload
    assert "publishability" not in payload
