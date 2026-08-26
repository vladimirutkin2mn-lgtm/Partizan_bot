from fastapi.testclient import TestClient

from app.customer_account import customer_account_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.growth_balance import growth_balance_service
from app.main import app
from app.runtime_store import get_runtime_store


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


def _create_second_project(client: TestClient) -> dict:
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
    return response.json()


def test_customer_session_can_create_and_open_a_second_project_without_browser_token() -> None:
    client, first = _registered_client()

    payload = _create_second_project(client)

    assert "customer_token" not in str(payload)
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
    assert created["brief"] == (
        "A Telegram channel for early-stage founders about practical customer acquisition."
    )
    assert created["budget_usd"] == 500

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


def test_existing_projects_get_navigation_fallback_names_and_original_description() -> None:
    client, preview = _registered_client()

    projects = client.get("/customer/account/projects")

    assert projects.status_code == 200
    item = next(project for project in projects.json() if project["project_id"] == str(preview.project_id))
    assert item["name"] == "United States · Get paying customers"
    assert item["project_type"] is None
    assert item["brief"] == "AI bookkeeping assistant for US freelancers with a monthly subscription."
    assert item["budget_usd"] == 1000


def test_customer_can_soft_delete_project_and_access_is_revoked() -> None:
    client, first = _registered_client()
    created = _create_second_project(client)
    second_id = created["project_id"]

    response = client.delete(f"/customer/account/projects/{second_id}")

    assert response.status_code == 204
    projects = client.get("/customer/account/projects")
    assert projects.status_code == 200
    assert [item["project_id"] for item in projects.json()] == [str(first.project_id)]

    account = client.get("/customer/account/me")
    assert account.status_code == 200
    assert [item["project_id"] for item in account.json()["projects"]] == [str(first.project_id)]

    deleted_workspace = client.get(f"/customer/workspace/{second_id}")
    assert deleted_workspace.status_code == 403

    stored = get_runtime_store().get(CUSTOMER_PROJECT_NAMESPACE, second_id)
    assert stored is not None
    assert stored["deleted_at"]
    assert stored["customer_brief"] == (
        "A Telegram channel for early-stage founders about practical customer acquisition."
    )


def test_project_delete_requires_ownership() -> None:
    client, _ = _registered_client()

    response = client.delete("/customer/account/projects/11111111-1111-1111-1111-111111111111")

    assert response.status_code == 404
