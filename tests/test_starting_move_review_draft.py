from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import app.customer_starting_move as customer_starting_move_module
import app.customer_starting_move_draft as customer_starting_move_draft_module
from app.broad_research import (
    BroadResearchEvidenceView,
    BroadResearchOpportunityView,
    ResearchExecutionStatus,
    ResearchSurface,
)
from app.customer_account import customer_account_service
from app.customer_channels import customer_channel_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.customer_starting_move import customer_starting_move_service
from app.customer_starting_move_draft import (
    CUSTOMER_STARTING_MOVE_DRAFT_NAMESPACE,
    CustomerStartingMoveDraftComposer,
    customer_starting_move_draft_service,
)
from app.distribution_execution_service import (
    DISTRIBUTION_ACTION_NAMESPACE,
    DISTRIBUTION_EXPERIMENT_NAMESPACE,
)
from app.growth_balance import growth_balance_service
from app.main import app
from app.models import ProductProfileStatus
from app.runtime_store import get_runtime_store
from app.schemas import ProductProfileView

PRODUCT_ID = UUID("33333333-3333-4333-8333-333333333333")


@pytest.fixture(autouse=True)
def reset_review_draft_state():
    store = get_runtime_store()
    previous_meta_public_ready = customer_channel_service._settings.meta_oauth_public_ready
    customer_channel_service._settings.meta_oauth_public_ready = True
    customer_account_service.reset()
    customer_funnel_service.reset()
    customer_starting_move_service.reset()
    customer_starting_move_draft_service.reset()
    growth_balance_service.reset()
    store.clear_namespace(DISTRIBUTION_ACTION_NAMESPACE)
    store.clear_namespace(DISTRIBUTION_EXPERIMENT_NAMESPACE)
    original_composer = customer_starting_move_draft_service._composer
    customer_starting_move_draft_service._composer = CustomerStartingMoveDraftComposer(None)
    try:
        yield
    finally:
        customer_starting_move_draft_service._composer = original_composer
        customer_starting_move_draft_service.reset()
        store.clear_namespace(DISTRIBUTION_ACTION_NAMESPACE)
        store.clear_namespace(DISTRIBUTION_EXPERIMENT_NAMESPACE)
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
            "email": "review-draft@example.com",
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


def _scoped_opportunity() -> BroadResearchOpportunityView:
    return BroadResearchOpportunityView(
        id=uuid4(),
        product_id=PRODUCT_ID,
        icp_id=uuid4(),
        surface=ResearchSurface.COMMUNITY,
        kind="PUBLIC_COMMUNITY",
        title="Freelancer bookkeeping discussion",
        url="https://www.reddit.com/r/freelance/",
        rationale="Freelancers are discussing bookkeeping workflow pain in this community.",
        relevance_score=92,
        execution_status=ResearchExecutionStatus.MANUAL_HANDOFF,
        execution_requirement="Research evidence only; execution remains separately permissioned.",
        provenance=[
            BroadResearchEvidenceView(
                query="freelancer bookkeeping reddit",
                title="Freelancer bookkeeping discussion",
                url="https://www.reddit.com/r/freelance/",
                snippet="Freelancers compare bookkeeping workflows and recurring admin pain.",
            )
        ],
    )


def _make_ready_move(client: TestClient, preview: object, monkeypatch) -> dict:
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

    async def discover_selected(_product, _icp_result, _platform):
        return _scoped_opportunity()

    monkeypatch.setattr(
        customer_starting_move_service._broad_research,
        "discover_platform_preview",
        discover_selected,
    )
    researched = client.post(
        f"/customer/workspace/{preview.project_id}/starting-move/research"
    )
    assert researched.status_code == 200
    assert researched.json()["state"] == "READY"
    return researched.json()


def test_review_draft_requires_customer_session() -> None:
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
        f"/customer/workspace/{preview.project_id}/starting-move/draft"
    )

    assert response.status_code == 401


def test_review_draft_requires_ready_evidence() -> None:
    client, preview = _registered_client()
    _attach_product(preview)
    selected = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "REDDIT"},
    )
    assert selected.status_code == 200

    response = client.post(f"/customer/workspace/{preview.project_id}/starting-move/draft")

    assert response.status_code == 409
    assert "Research the selected channel" in response.json()["detail"]


def test_review_draft_uses_server_selection_and_creates_no_execution_state(
    monkeypatch,
) -> None:
    client, preview = _registered_client()
    _make_ready_move(client, preview, monkeypatch)
    monkeypatch.setattr(
        customer_starting_move_draft_module.product_intake_service,
        "get_product",
        lambda _product_id: _product(),
    )
    store = get_runtime_store()
    project_before = store.get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
    assert project_before is not None
    assert store.list_namespace(DISTRIBUTION_ACTION_NAMESPACE) == []
    assert store.list_namespace(DISTRIBUTION_EXPERIMENT_NAMESPACE) == []

    response = client.post(
        f"/customer/workspace/{preview.project_id}/starting-move/draft",
        json={"platform": "TIKTOK"},
    )

    assert response.status_code == 200
    draft = response.json()
    assert draft["state"] == "REVIEW_ONLY"
    assert draft["platform"] == "REDDIT"
    assert draft["source_url"] == "https://www.reddit.com/r/freelance/"
    assert draft["source_title"] == "Freelancer bookkeeping discussion"
    assert draft["content_text"]
    assert draft["execution_allowed"] is False
    assert "Publishing" in draft["execution_requirement"]
    assert store.list_namespace(DISTRIBUTION_ACTION_NAMESPACE) == []
    assert store.list_namespace(DISTRIBUTION_EXPERIMENT_NAMESPACE) == []
    assert len(store.list_namespace(CUSTOMER_STARTING_MOVE_DRAFT_NAMESPACE)) == 1

    project_after = store.get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
    assert project_after == project_before
    assert project_after is not None
    assert project_after.get("channel_preferences") is None
    assert project_after.get("channel_publisher_modes") is None
    assert customer_channel_service.autonomous_platforms(project_after) == []

    cached = client.get(f"/customer/workspace/{preview.project_id}/starting-move/draft")
    assert cached.status_code == 200
    assert cached.json() == draft
