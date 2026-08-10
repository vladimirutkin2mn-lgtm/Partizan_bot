from uuid import uuid4

from app.llm import get_llm_provider
from app.models import ProductProfileStatus
from app.product_agent import ProductIntakeAgent
from app.product_intake import PRODUCT_INTAKE_NAMESPACE, InMemoryProductIntakeService
from app.runtime_store import MemoryRuntimeStateStore


def test_product_intake_hydrates_from_store_after_service_recreation() -> None:
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    store.put(
        PRODUCT_INTAKE_NAMESPACE,
        str(product_id),
        {
            "product": {
                "id": str(product_id),
                "input_brief": "Detailed Oracle product brief for persistence testing.",
                "name": "Oracle",
                "description": "AI entertainment relationship readings.",
                "problem_or_desire": "Relationship uncertainty",
                "value_proposition": "Personalized reflective readings",
                "usp": "Fast personalized experience",
                "use_cases": ["relationship reflection"],
                "market": "US",
                "language": "English",
                "price": 9.99,
                "pricing_model": "subscription",
                "goal": "Acquire paid users",
                "budget": 200.0,
                "max_cac": 5.0,
                "allowed_channels": ["Telegram", "Instagram", "Reddit", "TikTok"],
                "constraints": [],
                "known_audience": ["relationship advice seekers"],
                "known_competitors": [],
                "reference_links": ["https://example.com/oracle"],
                "assumptions": [],
                "contradictions": [],
                "status": ProductProfileStatus.CONFIRMED.value,
            },
            "brief": "Detailed Oracle product brief for persistence testing.",
            "reference_links": ["https://example.com/oracle"],
            "questions": [],
            "answers": [],
            "answered_fields": [],
        },
    )

    first = InMemoryProductIntakeService(ProductIntakeAgent(get_llm_provider()), store)
    assert first.get_product(product_id).name == "Oracle"

    second = InMemoryProductIntakeService(ProductIntakeAgent(get_llm_provider()), store)
    restored = second.get_product(product_id)
    assert restored.id == product_id
    assert restored.status == ProductProfileStatus.CONFIRMED
    assert restored.max_cac == 5.0
