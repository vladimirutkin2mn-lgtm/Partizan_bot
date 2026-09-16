from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.customer_account import customer_account_service
from app.customer_channels import customer_channel_service
from app.customer_funnel import customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.growth_balance import growth_balance_service
from app.main import app


@pytest.fixture(autouse=True)
def reset_customer_channel_state(monkeypatch):
    previous_meta_public_ready = customer_channel_service._settings.meta_oauth_public_ready
    monkeypatch.delenv("META_OAUTH_DOGFOOD_PROJECT_IDS", raising=False)
    customer_channel_service._settings.meta_oauth_public_ready = False
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
            "email": "meta-dogfood@example.com",
            "password": "correct-horse-42",
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )
    assert response.status_code == 200
    return client, preview


def test_allowlisted_project_exposes_meta_connection_while_public_gate_is_off(monkeypatch) -> None:
    client, preview = _registered_client()
    monkeypatch.setenv(
        "META_OAUTH_DOGFOOD_PROJECT_IDS",
        f"not-a-uuid, {preview.project_id}",
    )

    response = client.get(f"/customer/workspace/{preview.project_id}/channels")

    assert response.status_code == 200
    instagram = next(item for item in response.json() if item["platform"] == "INSTAGRAM")
    assert instagram["autonomous_execution_available"] is True
    assert instagram["execution_ready"] is False
    assert instagram["execution_blocker"] == "connect Meta first"


def test_non_allowlisted_project_remains_meta_pending_when_public_gate_is_off(monkeypatch) -> None:
    client, preview = _registered_client()
    monkeypatch.setenv("META_OAUTH_DOGFOOD_PROJECT_IDS", str(uuid4()))

    response = client.get(f"/customer/workspace/{preview.project_id}/channels")

    assert response.status_code == 200
    instagram = next(item for item in response.json() if item["platform"] == "INSTAGRAM")
    assert instagram["autonomous_execution_available"] is False
    assert instagram["execution_ready"] is False
    assert instagram["execution_blocker"] == "Meta customer connection is temporarily unavailable"
