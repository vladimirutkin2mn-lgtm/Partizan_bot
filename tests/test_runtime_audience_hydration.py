from uuid import uuid4

from app.audience_intelligence_service import (
    AUDIENCE_MAP_NAMESPACE,
    AUDIENCE_OPPORTUNITY_NAMESPACE,
    InMemoryAudienceIntelligenceService,
)
from app.runtime_store import MemoryRuntimeStateStore


def test_audience_distribution_hydrates_after_service_recreation() -> None:
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    opportunity_id = uuid4()
    opportunity = {
        "id": str(opportunity_id),
        "icp_id": str(uuid4()),
        "platform": "REDDIT",
        "kind": "SUBREDDIT",
        "canonical_key": "reddit:r/relationships",
        "title": "r/relationships",
        "url": "https://www.reddit.com/r/relationships/",
        "relevance_score": 91.0,
        "rationale": "Strong ICP overlap",
        "metadata": {"language": "English"},
        "evidence": [{"query": "relationship advice reddit"}],
        "legacy_channel_id": None,
    }
    store.put(
        AUDIENCE_MAP_NAMESPACE,
        str(product_id),
        {
            "product_id": str(product_id),
            "top_icp_count": 1,
            "opportunity_count": 1,
            "opportunities": [opportunity],
        },
    )
    store.put(AUDIENCE_OPPORTUNITY_NAMESPACE, str(opportunity_id), opportunity)

    first = InMemoryAudienceIntelligenceService(store)
    assert first.get(product_id).opportunity_count == 1

    second = InMemoryAudienceIntelligenceService(store)
    restored = second.get(product_id)
    assert restored.opportunities[0].platform.value == "REDDIT"
    assert second.find_opportunity(opportunity_id).title == "r/relationships"
