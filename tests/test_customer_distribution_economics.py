import pytest
from fastapi.testclient import TestClient

from app.customer_account import customer_account_service
from app.customer_funnel import customer_funnel_service
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.main import app
from app.product_intake import product_intake_service


@pytest.fixture(autouse=True)
def reset_state() -> None:
    customer_account_service.reset()
    customer_funnel_service.reset()
    product_intake_service.reset()
    distribution_play_service.reset()
    distribution_execution_service.reset()
    distribution_analytics_service.reset()


def _register(client: TestClient, email: str) -> str:
    preview = client.post(
        "/v1/customer-projects/preview",
        json={
            "brief": "AI bookkeeping assistant for independent consultants.",
            "market": "United States",
            "goal": "Get paying customers",
            "budget_usd": 500,
        },
    )
    assert preview.status_code == 201, preview.text
    payload = preview.json()
    registered = client.post(
        "/customer/account/register",
        json={
            "email": email,
            "password": "correct-horse-42",
            "project_id": payload["project_id"],
            "customer_token": payload["customer_token"],
        },
    )
    assert registered.status_code == 200, registered.text
    return payload["project_id"]


def test_customer_economics_is_account_scoped_and_hides_internal_operating_cost() -> None:
    client = TestClient(app)
    project_id = _register(client, "economics@example.com")

    response = client.get(f"/customer/workspace/{project_id}/distribution-economics")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["experiment_count"] == 0
    assert body["costs"] == {
        "research_fee": 0.0,
        "execution_fee": 0.0,
        "distribution_spend": 0.0,
        "total": 0.0,
    }
    assert "operating_cost" not in body["costs"]


def test_customer_cannot_read_another_projects_distribution_economics() -> None:
    owner = TestClient(app)
    project_id = _register(owner, "economics-owner@example.com")

    other = TestClient(app)
    _register(other, "economics-other@example.com")
    response = other.get(f"/customer/workspace/{project_id}/distribution-economics")

    assert response.status_code == 403
