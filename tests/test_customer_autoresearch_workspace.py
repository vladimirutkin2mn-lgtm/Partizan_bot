from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.customer_account import customer_account_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.growth_autoresearch import (
    GROWTH_AUTORESEARCH_CHAMPION_NAMESPACE,
    GROWTH_AUTORESEARCH_CURRENT_CHAMPION_NAMESPACE,
    GROWTH_AUTORESEARCH_EVALUATION_NAMESPACE,
    GROWTH_AUTORESEARCH_POLICY_NAMESPACE,
    GROWTH_AUTORESEARCH_TRIAL_NAMESPACE,
    growth_autoresearch_service,
)
from app.growth_autoresearch_loop import growth_autoresearch_loop_service
from app.growth_autoresearch_schemas import (
    GrowthResearchBaselineRequest,
    GrowthResearchEvidence,
    GrowthResearchPolicyRequest,
    GrowthVariantSpec,
)
from app.main import app
from app.runtime_store import get_runtime_store


@pytest.fixture(autouse=True)
def reset_state() -> None:
    customer_account_service.reset()
    customer_funnel_service.reset()
    growth_autoresearch_loop_service.reset()
    store = get_runtime_store()
    if store.ephemeral:
        for namespace in (
            GROWTH_AUTORESEARCH_POLICY_NAMESPACE,
            GROWTH_AUTORESEARCH_CURRENT_CHAMPION_NAMESPACE,
            GROWTH_AUTORESEARCH_CHAMPION_NAMESPACE,
            GROWTH_AUTORESEARCH_TRIAL_NAMESPACE,
            GROWTH_AUTORESEARCH_EVALUATION_NAMESPACE,
        ):
            store.clear_namespace(namespace)


def _registered_project(client: TestClient, *, email: str = "founder@example.com"):
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with subscription pricing.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
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


def _attach_autoresearch(project_id):
    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
    assert project is not None
    product_id = uuid4()
    project["product_id"] = str(product_id)
    project["research_state"] = "READY"
    project["status"] = "RESEARCH_READY"
    store.put(CUSTOMER_PROJECT_NAMESPACE, str(project_id), project)

    growth_autoresearch_service.configure_policy(
        product_id,
        GrowthResearchPolicyRequest(
            allowed_platforms=["META"],
            max_changed_dimensions=2,
            max_shadow_trial_budget=10,
            shadow_research_budget=40,
            max_trial_budget_share=0.5,
        ),
    )
    growth_autoresearch_service.establish_baseline(
        product_id,
        GrowthResearchBaselineRequest(
            variant=GrowthVariantSpec(
                platform="META",
                tactic_id="paid-social",
                audience="US freelancers",
                message_angle="Automate bookkeeping",
                test_budget=0,
            ),
            evidence=GrowthResearchEvidence(
                spend=100,
                visits=300,
                signups=30,
                activated_users=20,
                paid_users=10,
                revenue=500,
                source="measured-replay",
            ),
        ),
    )
    return product_id


def test_customer_autoresearch_api_is_session_owned_and_can_pause_resume() -> None:
    owner = TestClient(app)
    project = _registered_project(owner)
    product_id = _attach_autoresearch(project.project_id)

    overview = owner.get(f"/customer/workspace/{project.project_id}/autoresearch")
    assert overview.status_code == 200
    assert overview.json()["product_id"] == str(product_id)
    assert overview.json()["status"] == "IDLE"
    assert overview.json()["research_only"] is True

    stranger = TestClient(app)
    assert stranger.get(
        f"/customer/workspace/{project.project_id}/autoresearch"
    ).status_code == 401

    paused = owner.post(
        f"/customer/workspace/{project.project_id}/autoresearch/status",
        json={"status": "PAUSED"},
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "PAUSED"
    assert growth_autoresearch_service.get_policy(product_id).paused is True

    resumed = owner.post(
        f"/customer/workspace/{project.project_id}/autoresearch/status",
        json={"status": "ACTIVE"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["paused"] is False


def test_customer_autoresearch_requires_deep_research_product() -> None:
    client = TestClient(app)
    project = _registered_project(client)

    response = client.get(f"/customer/workspace/{project.project_id}/autoresearch")

    assert response.status_code == 409
    assert "Complete deep research" in response.json()["detail"]


def test_experiments_assets_are_versioned_and_no_store() -> None:
    client = TestClient(app)

    response = client.get("/workspace")

    assert response.status_code == 200
    html = response.text
    assert "/workspace/assets/workspace.experiments.v1.css?v=" in html
    assert "/workspace/assets/workspace.experiments.v1.js?v=" in html
    assert response.headers["cache-control"] == "no-store, max-age=0"

    script = client.get("/workspace/assets/workspace.experiments.v1.js")
    assert script.status_code == 200
    assert script.headers["cache-control"] == "no-store, max-age=0"
    assert "Continuous acquisition experiments" in script.text
    assert "never count as visits, conversions, customers" in script.text

    assert "Continuous learning · included" in script.text
    assert "Partizan keeps improving how it gets you customers." in script.text
    assert "autoresearch-overview-winner" in script.text
    assert "autoresearch-overview-test" in script.text
    assert "autoresearch-overview-learning" in script.text
    assert "Paid execution remains behind settlement and channel-permission gates." in script.text
    assert "item.platform || surfaceLabel(item.surface)" in script.text

    base_script = client.get("/workspace/assets/workspace.v1.js")
    assert base_script.status_code == 200
    assert "partizan:workspace-ready" in base_script.text

    css = client.get("/workspace/assets/workspace.experiments.v1.css")
    assert css.status_code == 200
    assert ".ar-overview" in css.text
    assert ".ar-overview-grid" in css.text
