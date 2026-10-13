from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.customer_account import customer_account_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_research_communities import AUDIENCE_MAP_NAMESPACE
from app.customer_schemas import CustomerPreviewRequest
from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.growth_balance import growth_balance_service
from app.main import app
from app.runtime_store import get_runtime_store
from app.telegram_community_restrictions import TELEGRAM_COMMUNITY_RESTRICTION_NAMESPACE


@pytest.fixture(autouse=True)
def reset_state() -> None:
    customer_account_service.reset()
    customer_funnel_service.reset()
    growth_balance_service.reset()
    store = get_runtime_store()
    store.clear_namespace(AUDIENCE_MAP_NAMESPACE)
    store.clear_namespace(TELEGRAM_COMMUNITY_RESTRICTION_NAMESPACE)


def _register(client: TestClient, email: str):
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="A useful product with a clearly defined niche audience.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=500,
        )
    )
    response = client.post(
        "/customer/account/register",
        json={
            "email": email,
            "password": "correct-horse-42",
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )
    assert response.status_code == 200
    return preview


def _seed_research_community(project_id) -> None:
    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
    assert project is not None
    product_id = uuid4()
    project["product_id"] = str(product_id)
    store.put(CUSTOMER_PROJECT_NAMESPACE, str(project_id), project)

    opportunity = DistributionOpportunityView(
        id=uuid4(),
        icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key="telegram:femdomgroup",
        title="FemDom community",
        url="https://t.me/FemdomGroup/777",
        relevance_score=92,
        rationale="Strong audience evidence, but no current action target is approved.",
        metadata={
            "opportunity_scope": "COMMUNITY",
            "actionability": "RESEARCH_ONLY",
            "handle": "FemdomGroup",
            "member_count": 4200,
            "action_target_url": "https://t.me/FemdomGroup/777",
            "action_target_specific": False,
        },
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


def test_owner_can_read_research_communities_without_publish_target_fields() -> None:
    owner = TestClient(app)
    preview = _register(owner, "research-owner@example.com")
    _seed_research_community(preview.project_id)

    response = owner.get(
        f"/customer/workspace/{preview.project_id}/research-communities"
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["title"] == "FemDom community"
    assert payload[0]["community_url"].rstrip("/") == "https://t.me/FemdomGroup"
    assert payload[0]["actionability"] == "RESEARCH_ONLY"
    assert "action_target_url" not in payload[0]
    assert "target_url" not in payload[0]
    assert "publishability" not in payload[0]
    assert "suggested_content" not in payload[0]


def test_research_communities_require_session_and_project_ownership() -> None:
    owner = TestClient(app)
    owner_preview = _register(owner, "research-owner@example.com")
    _seed_research_community(owner_preview.project_id)

    unauthenticated = TestClient(app)
    missing_session = unauthenticated.get(
        f"/customer/workspace/{owner_preview.project_id}/research-communities"
    )
    assert missing_session.status_code == 401

    intruder = TestClient(app)
    _register(intruder, "research-intruder@example.com")
    foreign = intruder.get(
        f"/customer/workspace/{owner_preview.project_id}/research-communities"
    )
    assert foreign.status_code == 403
