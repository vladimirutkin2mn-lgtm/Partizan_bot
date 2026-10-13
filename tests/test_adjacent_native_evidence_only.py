from types import SimpleNamespace
from uuid import uuid4

from app.discovery_adjacent_expansion import build_adjacent_discovery_hints
from app.distribution_types import DistributionPlatform


def _product():
    return SimpleNamespace(
        id=uuid4(),
        name="FemDom",
        problem_or_desire="find relevant FemDom communities",
        value_proposition="audience discovery",
        description="",
        known_audience=[],
    )


def _icps():
    return SimpleNamespace(
        icps=[
            SimpleNamespace(
                title="People interested in FemDom",
                description="FemDom audience",
                pain="find relevant FemDom communities",
                trigger="looking for FemDom spaces",
                desired_outcome="find FemDom peers",
                alternatives=[],
            )
        ]
    )


def _opportunity(*, metadata: dict | None = None, evidence: list[dict] | None = None):
    return SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        canonical_key="telegram:42:community",
        title="FemDom",
        metadata={
            "intelligence_scores": {
                "audience_fit": 80,
                "confidence": 80,
            },
            "relevance_guard": {"status": "PASS"},
            **(metadata or {}),
        },
        evidence=evidence or [],
    )


def test_generic_search_provider_snippet_cannot_create_adjacent_hint() -> None:
    opportunity = _opportunity(
        evidence=[
            {
                "title": "Search summary",
                "snippet": "approved explicitly members direct distribution targets",
                "url": "https://t.me/FemDom",
            }
        ]
    )

    hints = build_adjacent_discovery_hints(
        product=_product(),
        icps=_icps(),
        opportunities=[opportunity],
    )

    assert hints == []


def test_native_recent_context_can_create_adjacent_hint() -> None:
    opportunity = _opportunity(
        metadata={
            "about": "",
            "recent_context": [
                {
                    "text": "crypto women investors meetup tonight",
                    "url": "https://t.me/FemDom/123",
                }
            ],
        }
    )

    hints = build_adjacent_discovery_hints(
        product=_product(),
        icps=_icps(),
        opportunities=[opportunity],
    )

    assert hints
    assert "crypto" in hints[0]
    assert "women" in hints[0]
    assert "approved" not in hints[0]


def test_explicitly_tagged_telegram_native_evidence_can_supply_vocabulary() -> None:
    opportunity = _opportunity(
        evidence=[
            {
                "title": "FemDom",
                "snippet": "founders moscow networking evening",
                "url": "https://t.me/FemDom",
                "source_metadata": {"evidence_type": "telegram_native"},
            }
        ]
    )

    hints = build_adjacent_discovery_hints(
        product=_product(),
        icps=_icps(),
        opportunities=[opportunity],
    )

    assert hints
    assert "founders" in hints[0]
    assert "moscow" in hints[0]
