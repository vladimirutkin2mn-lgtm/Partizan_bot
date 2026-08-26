from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.broad_research import (
    BroadResearchService,
    ResearchExecutionStatus,
    ResearchSurface,
)
from app.distribution_types import DistributionPlatform
from app.models import ProductProfileStatus
from app.runtime_store import MemoryRuntimeStateStore
from app.schemas import (
    ICPGenerationResponse,
    ICPScoreBreakdownView,
    ICPView,
    ProductProfileView,
)
from app.search import DiscoveryQuery, MockSearchProvider, SearchHit, SearchProvider

PRODUCT_ID = UUID("11111111-1111-4111-8111-111111111111")


def _product() -> ProductProfileView:
    return ProductProfileView(
        id=PRODUCT_ID,
        input_brief="AI bookkeeping assistant for freelancers.",
        name="Ledger helper",
        description="AI bookkeeping assistant for freelancers.",
        problem_or_desire="Freelancers lose time managing bookkeeping and tax records.",
        value_proposition="Keep books organized with less manual work.",
        usp=None,
        use_cases=["bookkeeping"],
        market="United States",
        language="English",
        price=49,
        pricing_model="monthly",
        goal="Acquire paying customers",
        budget=1000,
        max_cac=25,
        allowed_channels=[],
        constraints=[],
        known_audience=[],
        known_competitors=[],
        reference_links=[],
        assumptions=[],
        contradictions=[],
        status=ProductProfileStatus.CONFIRMED,
    )


def _icp(rank: int, score: float) -> ICPView:
    return ICPView(
        id=uuid4(),
        product_id=PRODUCT_ID,
        rank=rank,
        title=f"Independent consultant segment {rank}",
        description="Independent professionals managing their own business admin.",
        pain="manual bookkeeping tax admin",
        desired_outcome="simple accurate books with less admin",
        trigger="tax deadline or growing client volume",
        willingness_to_pay="Pays for software that saves admin time",
        alternatives=["spreadsheet", "accountant"],
        message_hook="Spend less time on bookkeeping",
        score=score,
        score_breakdown=ICPScoreBreakdownView(
            pain_intensity=8,
            purchase_intent=8,
            willingness_to_pay=8,
            ease_of_targeting=8,
            market_size=8,
            competitive_headroom=7,
            speed_of_validation=8,
        ),
        score_explanation="Strong fit",
        rationale=["Clear pain"],
    )


def _icp_result() -> ICPGenerationResponse:
    icps = [_icp(index, 90 - index) for index in range(1, 11)]
    return ICPGenerationResponse(
        product_id=PRODUCT_ID,
        generated_count=10,
        ranked_count=10,
        icps=icps,
    )


@pytest.mark.asyncio
async def test_broad_research_discovers_all_first_class_surfaces_without_distribution_platforms() -> None:
    store = MemoryRuntimeStateStore()
    service = BroadResearchService(store, MockSearchProvider())

    result = await service.discover(_product(), _icp_result())

    surfaces = {item.surface for item in result.opportunities}
    assert surfaces == set(ResearchSurface)
    assert result.opportunity_count == len(result.opportunities)
    assert result.opportunity_count >= 12
    assert all(item.provenance for item in result.opportunities)
    assert all(item.execution_status != "PARTIZAN_CONTROL_PLANE" for item in result.opportunities)
    assert all(surface.value not in {platform.value for platform in DistributionPlatform} for surface in ResearchSurface)

    search_clusters = [item for item in result.opportunities if item.surface == ResearchSurface.SEARCH]
    assert search_clusters
    assert all(item.kind == "QUERY_CLUSTER" for item in search_clusters)
    assert all(item.url is None for item in search_clusters)
    assert all(len(item.provenance) >= 1 for item in search_clusters)


@pytest.mark.asyncio
async def test_broad_research_persists_and_can_be_reloaded_after_service_restart() -> None:
    store = MemoryRuntimeStateStore()
    first = BroadResearchService(store, MockSearchProvider())
    discovered = await first.discover(_product(), _icp_result())

    restarted = BroadResearchService(store, MockSearchProvider())
    loaded = restarted.get(PRODUCT_ID)

    assert loaded is not None
    assert loaded == discovered


class PartiallyFailingProvider(SearchProvider):
    def __init__(self) -> None:
        self._fallback = MockSearchProvider()

    async def search(self, discovery_query: DiscoveryQuery, limit: int = 5) -> list[SearchHit]:
        if "podcast publication media" in discovery_query.query:
            raise RuntimeError("one research surface is temporarily unavailable")
        return await self._fallback.search(discovery_query, limit)


@pytest.mark.asyncio
async def test_one_failed_public_web_surface_does_not_fabricate_or_abort_other_research() -> None:
    service = BroadResearchService(MemoryRuntimeStateStore(), PartiallyFailingProvider())

    result = await service.discover(_product(), _icp_result())

    assert ResearchSurface.MEDIA not in {item.surface for item in result.opportunities}
    assert ResearchSurface.CREATOR in {item.surface for item in result.opportunities}
    assert ResearchSurface.SEARCH in {item.surface for item in result.opportunities}
    assert all(item.provenance for item in result.opportunities)


def test_execution_requirements_are_explicit_and_never_claim_live_connection() -> None:
    assert ResearchExecutionStatus.OUTREACH_POSSIBLE.value == "OUTREACH_POSSIBLE"
    assert ResearchExecutionStatus.MANUAL_HANDOFF.value == "MANUAL_HANDOFF"
    assert ResearchExecutionStatus.RESEARCH_ONLY.value == "RESEARCH_ONLY"
