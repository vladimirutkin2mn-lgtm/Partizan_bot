from fastapi.testclient import TestClient
import pytest

from app.customer_account import customer_account_service
from app.customer_channels import customer_channel_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.growth_balance import growth_balance_service
from app.main import app
from app.runtime_store import get_runtime_store


@pytest.fixture(autouse=True)
def reset_customer_channel_execution_state():
    previous_meta_public_ready = customer_channel_service._settings.meta_oauth_public_ready
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
            "email": "execution-foundation@example.com",
            "password": "correct-horse-42",
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )
    assert response.status_code == 200
    return client, preview


def _by_platform(payload: list[dict], platform: str) -> dict:
    return next(item for item in payload if item["platform"] == platform)


def test_reddit_and_telegram_expose_fail_closed_execution_capabilities() -> None:
    client, preview = _registered_client()

    response = client.get(f"/customer/workspace/{preview.project_id}/channels")

    assert response.status_code == 200
    for platform in ("REDDIT", "TELEGRAM"):
        channel = _by_platform(response.json(), platform)
        assert channel["publisher_mode"] == "MANUAL"
        publisher_modes = {item["mode"]: item for item in channel["publisher_modes"]}
        assert publisher_modes["MANUAL"] == {
            "mode": "MANUAL",
            "available": True,
            "blocker": None,
        }
        assert publisher_modes["CLIENT_OWNED"]["available"] is False
        assert "not implemented" in publisher_modes["CLIENT_OWNED"]["blocker"]
        assert publisher_modes["PARTIZAN_MANAGED"]["available"] is False

        capabilities = {item["capability"]: item for item in channel["capabilities"]}
        assert capabilities["SEARCH"] == {
            "capability": "SEARCH",
            "ready": True,
            "blocker": None,
        }
        for capability in ("DRAFT", "PUBLISH", "MEASURE"):
            assert capabilities[capability]["ready"] is False
            assert capabilities[capability]["blocker"]


def test_publisher_mode_can_be_persisted_without_changing_channel_mode() -> None:
    client, preview = _registered_client()

    response = client.put(
        f"/customer/workspace/{preview.project_id}/channels",
        json={"channels": [{"platform": "REDDIT", "publisher_mode": "MANUAL"}]},
    )

    assert response.status_code == 200
    reddit = _by_platform(response.json(), "REDDIT")
    assert reddit["mode"] == "RESEARCH_ONLY"
    assert reddit["publisher_mode"] == "MANUAL"

    project = get_runtime_store().get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
    assert project is not None
    assert project["channel_preferences"]["REDDIT"] == "RESEARCH_ONLY"
    assert project["channel_publisher_modes"]["REDDIT"] == "MANUAL"


def test_unavailable_client_owned_publish_mode_fails_closed() -> None:
    client, preview = _registered_client()

    response = client.put(
        f"/customer/workspace/{preview.project_id}/channels",
        json={"channels": [{"platform": "REDDIT", "publisher_mode": "CLIENT_OWNED"}]},
    )

    assert response.status_code == 409
    assert "client-owned publish adapter is not implemented yet" in response.json()["detail"]
    current = client.get(f"/customer/workspace/{preview.project_id}/channels")
    assert current.status_code == 200
    assert _by_platform(current.json(), "REDDIT")["publisher_mode"] == "MANUAL"
