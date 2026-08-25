from fastapi.testclient import TestClient

from app.customer_account import customer_account_service
from app.customer_funnel import customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.growth_balance import growth_balance_service
from app.main import app


def _registered_client() -> tuple[TestClient, object]:
    customer_account_service.reset()
    customer_funnel_service.reset()
    growth_balance_service.reset()
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
            "email": "projects@example.com",
            "password": "correct-horse-42",
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )
    assert response.status_code == 200
    return client, preview


def test_customer_session_can_create_and_open_a_second_project_without_browser_token() -> None:
    client, first = _registered_client()

    response = client.post(
        "/customer/account/projects",
        json={
            "name": "Founder Growth Telegram",
            "project_type": "TELEGRAM_COMMUNITY",
            "reference_url": "https://t.me/founder_growth",
            "brief": "A Telegram channel for early-stage founders about practical customer acquisition.",
            "market": "United States",
            "goal": "Grow subscribers",
            "budget_usd": 500,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert "customer_token" not in response.text
    second_id = payload["project_id"]
    assert second_id != str(first.project_id)
    assert len(payload["account"]["projects"]) == 2

    projects = client.get("/customer/account/projects")
    assert projects.status_code == 200
    nav = projects.json()
    created = next(item for item in nav if item["project_id"] == second_id)
    assert created["name"] == "Founder Growth Telegram"
    assert created["project_type"] == "TELEGRAM_COMMUNITY"
    assert created["reference_url"].rstrip("/") == "https://t.me/founder_growth"

    workspace = client.get(f"/customer/workspace/{second_id}")
    assert workspace.status_code == 200
    assert workspace.json()["project"]["market"] == "United States"
    assert workspace.json()["project"]["goal"] == "Grow subscribers"


def test_project_creation_requires_customer_session() -> None:
    customer_account_service.reset()
    customer_funnel_service.reset()
    client = TestClient(app)

    response = client.post(
        "/customer/account/projects",
        json={
            "name": "New project",
            "project_type": "WEBSITE_PRODUCT",
            "reference_url": "https://example.com",
            "brief": "A useful product description that is long enough for validation.",
            "market": "United States",
            "goal": "Get paying customers",
            "budget_usd": 1000,
        },
    )

    assert response.status_code == 401


def test_existing_projects_get_navigation_fallback_names() -> None:
    client, preview = _registered_client()

    projects = client.get("/customer/account/projects")

    assert projects.status_code == 200
    item = next(project for project in projects.json() if project["project_id"] == str(preview.project_id))
    assert item["name"] == "United States · Get paying customers"
    assert item["project_type"] is None
