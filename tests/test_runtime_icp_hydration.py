from uuid import uuid4

from app.icp_service import ICP_NAMESPACE, InMemoryICPService
from app.runtime_store import MemoryRuntimeStateStore


def test_icp_result_hydrates_from_store_after_service_recreation() -> None:
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    icps = []
    for rank in range(1, 11):
        icps.append(
            {
                "id": str(uuid4()),
                "product_id": str(product_id),
                "rank": rank,
                "title": f"ICP {rank}",
                "description": "People seeking relationship clarity",
                "pain": "Uncertainty",
                "desired_outcome": "Clarity",
                "trigger": "Relationship event",
                "willingness_to_pay": "Moderate",
                "alternatives": ["tarot"],
                "message_hook": "Get a fresh perspective",
                "score": 80.0,
                "score_breakdown": {
                    "pain_intensity": 8,
                    "purchase_intent": 7,
                    "willingness_to_pay": 6,
                    "ease_of_targeting": 8,
                    "market_size": 8,
                    "competitive_headroom": 6,
                    "speed_of_validation": 8,
                },
                "score_explanation": "Good test segment",
                "rationale": ["Relevant audience"],
                "duplicate_of": None,
            }
        )
    store.put(
        ICP_NAMESPACE,
        str(product_id),
        {
            "product_id": str(product_id),
            "generated_count": 10,
            "ranked_count": 10,
            "icps": icps,
            "duplicate_clusters": [],
        },
    )

    first = InMemoryICPService(store)
    assert first.get(product_id).ranked_count == 10

    second = InMemoryICPService(store)
    restored = second.get(product_id)
    assert restored.product_id == product_id
    assert len(restored.icps) == 10
