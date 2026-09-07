import pytest
from fastapi.testclient import TestClient

from app.customer_account import customer_account_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.growth_balance import growth_balance_service
from app.main import app
from app.runtime_store import get_runtime_store


@pytest.fixture(autouse=True)
def reset_customer_channel_selection_state() -> None:
    customer_account_service.reset()
    customer_funnel_service.reset()
    growth_balance_service.reset()


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
            "email": "channel-choice@example.com",
            "password": "correct-horse-42",
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )
    assert response.status_code == 200
    return client, preview


def test_channel_selection_requires_customer_session() -> None:
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with a monthly subscription.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
    )

    response = TestClient(app).put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "TELEGRAM"},
    )

    assert response.status_code == 401


def test_channels_start_unselected_even_though_research_is_enabled() -> None:
    client, preview = _registered_client()

    response = client.get(f"/customer/workspace/{preview.project_id}/channels")

    assert response.status_code == 200
    channels = response.json()
    assert all(item["mode"] == "RESEARCH_ONLY" for item in channels)
    assert all(item["selected"] is False for item in channels)


def test_customer_selects_channel_without_granting_execution_or_spend_permission() -> None:
    client, preview = _registered_client()

    response = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "TELEGRAM"},
    )

    assert response.status_code == 200
    channels = response.json()
    telegram = next(item for item in channels if item["platform"] == "TELEGRAM")
    assert telegram["selected"] is True
    assert telegram["mode"] == "RESEARCH_ONLY"
    assert telegram["autonomous_execution_available"] is False
    assert sum(item["selected"] for item in channels) == 1
    assert all(item["mode"] == "RESEARCH_ONLY" for item in channels)

    project = get_runtime_store().get(
        CUSTOMER_PROJECT_NAMESPACE,
        str(preview.project_id),
    )
    assert project is not None
    assert project["selected_acquisition_channel"] == "TELEGRAM"
    assert project["acquisition_channel_selected_at"]

    persisted = client.get(f"/customer/workspace/{preview.project_id}/channels")
    assert persisted.status_code == 200
    assert next(
        item for item in persisted.json() if item["platform"] == "TELEGRAM"
    )["selected"] is True


def test_selecting_a_different_channel_changes_intent_only() -> None:
    client, preview = _registered_client()

    first = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "TELEGRAM"},
    )
    assert first.status_code == 200

    second = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "INSTAGRAM"},
    )

    assert second.status_code == 200
    channels = second.json()
    assert next(item for item in channels if item["platform"] == "INSTAGRAM")["selected"] is True
    assert next(item for item in channels if item["platform"] == "TELEGRAM")["selected"] is False
    assert all(item["mode"] == "RESEARCH_ONLY" for item in channels)


def test_customer_cannot_select_a_channel_they_turned_off() -> None:
    client, preview = _registered_client()
    disabled = client.put(
        f"/customer/workspace/{preview.project_id}/channels",
        json={"channels": [{"platform": "REDDIT", "mode": "OFF"}]},
    )
    assert disabled.status_code == 200

    response = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "REDDIT"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Reddit is turned off. Turn it back on before selecting it."
    current = client.get(f"/customer/workspace/{preview.project_id}/channels").json()
    assert all(item["selected"] is False for item in current)
