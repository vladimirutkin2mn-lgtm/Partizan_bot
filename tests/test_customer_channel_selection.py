from fastapi.testclient import TestClient
import pytest

import app.customer_channel_routes as customer_channel_routes_module
from app.customer_account import customer_account_service
from app.customer_channels import customer_channel_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.growth_balance import growth_balance_service
from app.main import app
from app.runtime_store import get_runtime_store


@pytest.fixture(autouse=True)
def reset_customer_channel_selection_state():
    previous_meta_public_ready = customer_channel_service._settings.meta_oauth_public_ready
    customer_channel_service._settings.meta_oauth_public_ready = True
    customer_account_service.reset()
    customer_funnel_service.reset()
    growth_balance_service.reset()
    try:
        yield
    finally:
        customer_channel_service._settings.meta_oauth_public_ready = previous_meta_public_ready


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
        json={"platform": "REDDIT"},
    )

    assert response.status_code == 401


def test_selecting_starting_channel_does_not_enable_execution_or_refresh_policy(monkeypatch) -> None:
    client, preview = _registered_client()

    def unexpected_refresh(*_args, **_kwargs):
        raise AssertionError("starting-channel intent must not rebuild execution policy")

    monkeypatch.setattr(
        customer_channel_routes_module.customer_autopilot_service,
        "refresh_channel_policy",
        unexpected_refresh,
    )

    response = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "REDDIT"},
    )

    assert response.status_code == 200
    channels = {item["platform"]: item for item in response.json()}
    assert channels["REDDIT"]["selected"] is True
    assert sum(bool(item["selected"]) for item in channels.values()) == 1
    assert all(item["mode"] == "RESEARCH_ONLY" for item in channels.values())
    assert all(item["publisher_mode"] == "MANUAL" for item in channels.values())

    project = get_runtime_store().get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
    assert project is not None
    assert project["selected_acquisition_channel"] == "REDDIT"
    assert project.get("channel_preferences") is None
    assert project.get("channel_publisher_modes") is None
    assert customer_channel_service.autonomous_platforms(project) == []


def test_off_channel_cannot_be_selected() -> None:
    client, preview = _registered_client()
    turned_off = client.put(
        f"/customer/workspace/{preview.project_id}/channels",
        json={"channels": [{"platform": "TELEGRAM", "mode": "OFF"}]},
    )
    assert turned_off.status_code == 200

    response = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "TELEGRAM"},
    )

    assert response.status_code == 409
    assert "turned off" in response.json()["detail"]
    current = client.get(f"/customer/workspace/{preview.project_id}/channels")
    assert current.status_code == 200
    assert not any(item["selected"] for item in current.json())


def test_turning_selected_channel_off_clears_only_the_selection_intent() -> None:
    client, preview = _registered_client()
    selected = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "REDDIT"},
    )
    assert selected.status_code == 200

    turned_off = client.put(
        f"/customer/workspace/{preview.project_id}/channels",
        json={"channels": [{"platform": "REDDIT", "mode": "OFF"}]},
    )

    assert turned_off.status_code == 200
    channels = {item["platform"]: item for item in turned_off.json()}
    assert channels["REDDIT"]["mode"] == "OFF"
    assert channels["REDDIT"]["selected"] is False
    assert not any(item["selected"] for item in channels.values())

    project = get_runtime_store().get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
    assert project is not None
    assert "selected_acquisition_channel" not in project
    assert "acquisition_channel_selected_at" not in project
    assert project["channel_preferences"]["REDDIT"] == "OFF"
    assert customer_channel_service.autonomous_platforms(project) == []
