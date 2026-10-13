from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.customer_research_communities import (
    AUDIENCE_MAP_NAMESPACE,
    CUSTOMER_PROJECT_NAMESPACE,
    CustomerResearchCommunityService,
)
from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_community_restrictions import TelegramCommunityRestrictionMemory


def _opportunity(
    *,
    title: str = "FemDom community",
    url: str = "https://t.me/FemdomGroup",
    scope: str = "COMMUNITY",
    actionability: str = "RESEARCH_ONLY",
    score: float = 88.0,
    handle: str = "FemdomGroup",
) -> DistributionOpportunityView:
    return DistributionOpportunityView(
        id=uuid4(),
        icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key=f"telegram:{handle.casefold()}",
        title=title,
        url=url,
        relevance_score=score,
        rationale="The community contains audience evidence relevant to the product.",
        metadata={
            "opportunity_scope": scope,
            "actionability": actionability,
            "handle": handle,
            "member_count": 4200,
            "source_checked_at": "2026-10-12T18:00:00+00:00",
            "action_target_url": f"{url}/777",
            "action_target_specific": scope == "POST",
        },
    )


def _seed_project(
    store: MemoryRuntimeStateStore,
    *,
    project_id: UUID,
    product_id: UUID,
    opportunities: list[DistributionOpportunityView],
) -> None:
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {"id": str(project_id), "product_id": str(product_id)},
    )
    audience_map = AudienceDistributionMapView(
        product_id=product_id,
        top_icp_count=1,
        opportunity_count=len(opportunities),
        opportunities=opportunities,
    )
    store.put(
        AUDIENCE_MAP_NAMESPACE,
        str(product_id),
        audience_map.model_dump(mode="json"),
    )


def test_lists_only_research_only_telegram_communities_for_project() -> None:
    store = MemoryRuntimeStateStore()
    service = CustomerResearchCommunityService(store)
    project_id = uuid4()
    product_id = uuid4()
    community = _opportunity(score=91.0)
    concrete_post = _opportunity(
        title="Fresh actionable post",
        scope="POST",
        actionability="NEEDS_PREFLIGHT",
        score=99.0,
    )
    _seed_project(
        store,
        project_id=project_id,
        product_id=product_id,
        opportunities=[concrete_post, community],
    )

    items = service.list_for_project(project_id)

    assert len(items) == 1
    assert items[0].opportunity_id == community.id
    assert items[0].actionability == "RESEARCH_ONLY"
    assert items[0].write_access == "UNKNOWN"
    assert items[0].relevance_score == 91.0
    assert items[0].member_count == 4200
    assert items[0].source_checked_at == datetime(2026, 10, 12, 18, 0, tzinfo=UTC)


def test_research_view_exposes_only_base_community_url_not_action_target() -> None:
    store = MemoryRuntimeStateStore()
    service = CustomerResearchCommunityService(store)
    project_id = uuid4()
    product_id = uuid4()
    community = _opportunity(url="https://t.me/FemdomGroup/777")
    _seed_project(
        store,
        project_id=project_id,
        product_id=product_id,
        opportunities=[community],
    )

    item = service.list_for_project(project_id)[0]
    payload = item.model_dump(mode="json")

    assert str(item.community_url).rstrip("/") == "https://t.me/FemdomGroup"
    assert "action_target_url" not in payload
    assert "publish" not in " ".join(payload.keys()).casefold()


def test_known_write_restriction_does_not_hide_research_community() -> None:
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    product_id = uuid4()
    _seed_project(
        store,
        project_id=project_id,
        product_id=product_id,
        opportunities=[_opportunity()],
    )
    TelegramCommunityRestrictionMemory(store).remember(
        project_id,
        "FemdomGroup",
        reason="WRITE_FORBIDDEN",
        restriction_signal="WRITE_RESTRICTED",
    )

    item = CustomerResearchCommunityService(store).list_for_project(project_id)[0]

    assert item.actionability == "RESEARCH_ONLY"
    assert item.write_access == "NO_WRITE_ACCESS"
    assert item.write_access_detail is not None
    assert "WRITE_FORBIDDEN" in item.write_access_detail
    assert "audience evidence" in item.write_access_detail


def test_research_communities_are_project_scoped() -> None:
    store = MemoryRuntimeStateStore()
    service = CustomerResearchCommunityService(store)
    first_project = uuid4()
    second_project = uuid4()
    first_product = uuid4()
    second_product = uuid4()
    first = _opportunity(title="First project community", handle="FemdomGroup")
    second = _opportunity(
        title="Second project community",
        url="https://t.me/kinkyhive",
        handle="kinkyhive",
    )
    _seed_project(
        store,
        project_id=first_project,
        product_id=first_product,
        opportunities=[first],
    )
    _seed_project(
        store,
        project_id=second_project,
        product_id=second_product,
        opportunities=[second],
    )

    assert [item.title for item in service.list_for_project(first_project)] == [
        "First project community"
    ]
    assert [item.title for item in service.list_for_project(second_project)] == [
        "Second project community"
    ]


def test_missing_project_or_audience_map_returns_empty_research_view() -> None:
    store = MemoryRuntimeStateStore()
    service = CustomerResearchCommunityService(store)

    assert service.list_for_project(uuid4()) == []

    project_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {"id": str(project_id), "product_id": str(uuid4())},
    )
    assert service.list_for_project(project_id) == []
