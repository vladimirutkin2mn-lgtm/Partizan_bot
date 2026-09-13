from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import app.customer_starting_move as customer_starting_move_module
from app.broad_research import (
    BroadResearchEvidenceView,
    BroadResearchOpportunityView,
    BroadResearchService,
    PreviewResearchUnavailableError,
    ResearchExecutionStatus,
    ResearchSurface,
)
from app.customer_account import customer_account_service
from app.customer_channels import customer_channel_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.customer_starting_move import customer_starting_move_service
from app.distribution_types import DistributionPlatform
from app.growth_balance import growth_balance_service
from app.main import app
from app.models import ProductProfileStatus
from app.runtime_store import MemoryRuntimeStateStore, get_runtime_store
from app.schemas import (
    ICPGenerationResponse,
    ICPScoreBreakdownView,
    ICPView,
    ProductProfileView,
)
from app.search import DiscoveryQuery, MockSearchProvider, SearchHit, SearchProvider, SourceClass

PRODUCT_ID = UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture(autouse=True)
def reset_selected_channel_research_state():
    previous_meta_public_ready = customer_channel_service._settings.meta_oauth_public_ready
    customer_channel_service._settings.meta_oauth_public_ready = True
    customer_account_service.reset()
    customer_funnel_service.reset()
    customer_starting_move_service.reset()
    growth_balance_service.reset()
    try:
        yield
    finally:
        customer_starting_move_service.reset()
        customer_channel_service._settings.meta_oauth_public_ready = previous_meta_public_ready


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


def _icp_result() -> ICPGenerationResponse:
    icps = [
        ICPView(
            id=uuid4(),
            product_id=PRODUCT_ID,
            rank=rank,
            title=f"Independent consultants {rank}",
            description="Independent professionals managing their own business admin.",
            pain="manual bookkeeping tax admin",
            desired_outcome="simple accurate books with less admin",
            trigger="tax deadline or growing client volume",
            willingness_to_pay="Pays for software that saves admin time",
            alternatives=["spreadsheet", "accountant"],
            message_hook="Spend less time on bookkeeping",
            score=92 - rank,
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
        for rank in range(1, 11)
    ]
    return ICPGenerationResponse(
        product_id=PRODUCT_ID,
        generated_count=10,
        ranked_count=10,
        icps=icps,
    )


class StaticProvider(SearchProvider):
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.queries: list[DiscoveryQuery] = []

    async def search(self, discovery_query: DiscoveryQuery, limit: int = 5) -> list[SearchHit]:
        self.queries.append(discovery_query)
        return self.hits[:limit]


def _hit(url: str, source_class: SourceClass = SourceClass.COMMUNITY) -> SearchHit:
    return SearchHit(
        title="Concrete public opportunity",
        url=url,
        snippet="Freelancers discuss bookkeeping workflow pain here.",
        query="bookkeeping freelancers",
        source_class=source_class,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform", "site_filter", "url", "source_class"),
    [
        (
            DistributionPlatform.REDDIT,
            "site:reddit.com",
            "https://www.reddit.com/r/freelance/",
            SourceClass.COMMUNITY,
        ),
        (
            DistributionPlatform.TELEGRAM,
            "site:t.me",
            "https://t.me/freelancebooks",
            SourceClass.COMMUNITY,
        ),
        (
            DistributionPlatform.INSTAGRAM,
            "site:instagram.com",
            "https://www.instagram.com/freelancebooks/",
            SourceClass.CREATOR,
        ),
        (
            DistributionPlatform.TIKTOK,
            "site:tiktok.com",
            "https://www.tiktok.com/@freelancebooks/video/123",
            SourceClass.CREATOR,
        ),
    ],
)
async def test_platform_research_is_domain_scoped_and_evidence_backed(
    platform: DistributionPlatform,
    site_filter: str,
    url: str,
    source_class: SourceClass,
) -> None:
    provider = StaticProvider([_hit(url, source_class)])
    service = BroadResearchService(MemoryRuntimeStateStore(), provider)

    opportunity = await service.discover_platform_preview(
        _product(),
        _icp_result(),
        platform,
    )

    assert opportunity is not None
    assert opportunity.url == url
    assert opportunity.provenance
    assert len(provider.queries) == 1
    assert site_filter in provider.queries[0].query
    assert provider.queries[0].source_class == source_class


@pytest.mark.asyncio
async def test_platform_research_rejects_lookalike_domain_instead_of_fabricating_match() -> None:
    provider = StaticProvider([_hit("https://reddit.com.example.test/fake")])
    service = BroadResearchService(MemoryRuntimeStateStore(), provider)

    opportunity = await service.discover_platform_preview(
        _product(),
        _icp_result(),
        DistributionPlatform.REDDIT,
    )

    assert opportunity is None


@pytest.mark.asyncio
async def test_platform_research_never_falls_back_to_mock_evidence() -> None:
    service = BroadResearchService(MemoryRuntimeStateStore(), MockSearchProvider())

    with pytest.raises(PreviewResearchUnavailableError, match="not configured"):
        await service.discover_platform_preview(
            _product(),
            _icp_result(),
            DistributionPlatform.REDDIT,
        )


def _registered_client() -> tuple[TestClient, object]:
    client = TestClient(app)
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with a monthly subscription.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
    )
    response = client.post(
        "/customer/account/register",
        json={
            "email": "scoped-research@example.com",
            "password": "correct-horse-42",
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )
    assert response.status_code == 200
    return client, preview


def _attach_product(preview: object) -> None:
    project = get_runtime_store().get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
    assert project is not None
    project["product_id"] = str(PRODUCT_ID)
    get_runtime_store().put(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id), project)


def _scoped_opportunity(platform: DistributionPlatform) -> BroadResearchOpportunityView:
    return BroadResearchOpportunityView(
        id=uuid4(),
        product_id=PRODUCT_ID,
        icp_id=uuid4(),
        surface=(
            ResearchSurface.COMMUNITY
            if platform in {DistributionPlatform.REDDIT, DistributionPlatform.TELEGRAM}
            else ResearchSurface.CREATOR
        ),
        kind="PUBLIC_COMMUNITY",
        title="Selected-channel evidence",
        url="https://www.reddit.com/r/freelance/",
        rationale="This audience is discussing the problem on the selected channel.",
        relevance_score=92,
        execution_status=ResearchExecutionStatus.MANUAL_HANDOFF,
        execution_requirement="Research evidence only; execution remains separately permissioned.",
        provenance=[
            BroadResearchEvidenceView(
                query="freelancer bookkeeping reddit",
                title="Selected-channel evidence",
                url="https://www.reddit.com/r/freelance/",
                snippet="Freelancers discuss bookkeeping here.",
            )
        ],
    )


def test_scoped_research_requires_customer_session() -> None:
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with a monthly subscription.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
    )

    response = TestClient(app).post(
        f"/customer/workspace/{preview.project_id}/starting-move/research"
    )

    assert response.status_code == 401


def test_scoped_research_requires_an_existing_server_side_channel_selection() -> None:
    client, preview = _registered_client()
    _attach_product(preview)

    response = client.post(
        f"/customer/workspace/{preview.project_id}/starting-move/research",
        json={"platform": "TELEGRAM"},
    )

    assert response.status_code == 409
    assert "Choose a starting channel" in response.json()["detail"]


def test_scoped_research_uses_selected_channel_and_does_not_create_execution_intent(
    monkeypatch,
) -> None:
    client, preview = _registered_client()
    _attach_product(preview)
    selected = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "REDDIT"},
    )
    assert selected.status_code == 200

    project_before = get_runtime_store().get(
        CUSTOMER_PROJECT_NAMESPACE,
        str(preview.project_id),
    )
    assert project_before is not None
    seen_platforms: list[DistributionPlatform] = []

    monkeypatch.setattr(
        customer_starting_move_module.product_intake_service,
        "get_product",
        lambda _product_id: object(),
    )
    monkeypatch.setattr(
        customer_starting_move_module.icp_service,
        "get",
        lambda _product_id: object(),
    )

    async def discover_selected(_product, _icp_result, platform):
        seen_platforms.append(platform)
        return _scoped_opportunity(platform)

    monkeypatch.setattr(
        customer_starting_move_service._broad_research,
        "discover_platform_preview",
        discover_selected,
    )

    response = client.post(
        f"/customer/workspace/{preview.project_id}/starting-move/research",
        json={"platform": "TELEGRAM"},
    )

    assert response.status_code == 200
    move = response.json()
    assert seen_platforms == [DistributionPlatform.REDDIT]
    assert move["platform"] == "REDDIT"
    assert move["state"] == "READY"
    assert move["source"] == "CHANNEL_RESEARCH"
    assert move["title"] == "Selected-channel evidence"

    project_after = get_runtime_store().get(
        CUSTOMER_PROJECT_NAMESPACE,
        str(preview.project_id),
    )
    assert project_after == project_before
    assert project_after is not None
    assert project_after.get("channel_preferences") is None
    assert project_after.get("channel_publisher_modes") is None
    assert customer_channel_service.autonomous_platforms(project_after) == []

    cached = client.get(f"/customer/workspace/{preview.project_id}/starting-move")
    assert cached.status_code == 200
    assert cached.json()["source"] == "CHANNEL_RESEARCH"


def test_scoped_research_no_match_stays_research_only_and_is_remembered(monkeypatch) -> None:
    client, preview = _registered_client()
    _attach_product(preview)
    selected = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "REDDIT"},
    )
    assert selected.status_code == 200

    monkeypatch.setattr(
        customer_starting_move_module.product_intake_service,
        "get_product",
        lambda _product_id: object(),
    )
    monkeypatch.setattr(
        customer_starting_move_module.icp_service,
        "get",
        lambda _product_id: object(),
    )

    async def no_match(_product, _icp_result, _platform):
        return None

    monkeypatch.setattr(
        customer_starting_move_service._broad_research,
        "discover_platform_preview",
        no_match,
    )

    response = client.post(
        f"/customer/workspace/{preview.project_id}/starting-move/research"
    )

    assert response.status_code == 200
    assert response.json()["state"] == "NEEDS_RESEARCH"
    assert "No strong" in response.json()["title"]
    cached = client.get(f"/customer/workspace/{preview.project_id}/starting-move")
    assert cached.status_code == 200
    assert cached.json()["state"] == "NEEDS_RESEARCH"
    assert "Partizan searched" in cached.json()["rationale"]
