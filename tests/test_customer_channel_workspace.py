from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.customer_channel_routes as customer_channel_routes_module
import app.customer_channels as customer_channels_module
from app.customer_account import customer_account_service
from app.customer_channels import customer_channel_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.growth_balance import growth_balance_service
from app.main import app
from app.runtime_store import get_runtime_store


@pytest.fixture(autouse=True)
def reset_customer_channel_state() -> None:
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
            "email": "channels@example.com",
            "password": "correct-horse-42",
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )
    assert response.status_code == 200
    return client, preview


def test_channel_controls_require_customer_session() -> None:
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with a monthly subscription.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
    )
    stranger = TestClient(app)

    response = stranger.get(f"/customer/workspace/{preview.project_id}/channels")

    assert response.status_code == 401


def test_channel_controls_default_to_meta_auto_and_other_surfaces_research_only() -> None:
    client, preview = _registered_client()

    response = client.get(f"/customer/workspace/{preview.project_id}/channels")

    assert response.status_code == 200
    channels = {item["platform"]: item for item in response.json()}
    assert set(channels) == {"INSTAGRAM", "TIKTOK", "REDDIT", "TELEGRAM"}
    assert channels["INSTAGRAM"]["mode"] == "AUTO"
    assert channels["INSTAGRAM"]["autonomous_execution_available"] is True
    for platform in ("TIKTOK", "REDDIT", "TELEGRAM"):
        assert channels[platform]["mode"] == "RESEARCH_ONLY"
        assert channels[platform]["autonomous_execution_available"] is False


def test_customer_can_turn_channels_off_and_policy_persists() -> None:
    client, preview = _registered_client()

    reddit = client.put(
        f"/customer/workspace/{preview.project_id}/channels",
        json={"channels": [{"platform": "REDDIT", "mode": "OFF"}]},
    )
    assert reddit.status_code == 200
    assert next(item for item in reddit.json() if item["platform"] == "REDDIT")["mode"] == "OFF"

    instagram = client.put(
        f"/customer/workspace/{preview.project_id}/channels",
        json={"channels": [{"platform": "INSTAGRAM", "mode": "OFF"}]},
    )
    assert instagram.status_code == 200

    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
    assert project is not None
    assert project["channel_preferences"]["REDDIT"] == "OFF"
    assert project["channel_preferences"]["INSTAGRAM"] == "OFF"
    assert customer_channel_service.autonomous_platforms(project) == []


def test_channel_edits_do_not_resume_or_rebuild_a_customer_paused_autopilot(monkeypatch) -> None:
    client, preview = _registered_client()
    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
    assert project is not None
    project["autopilot_pause_reason"] = "CUSTOMER"
    store.put(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id), project)

    def unexpected_refresh(*_args, **_kwargs):
        raise AssertionError("manual customer pause must survive channel edits")

    monkeypatch.setattr(
        customer_channel_routes_module.customer_autopilot_service,
        "refresh_channel_policy",
        unexpected_refresh,
    )

    response = client.put(
        f"/customer/workspace/{preview.project_id}/channels",
        json={"channels": [{"platform": "REDDIT", "mode": "OFF"}]},
    )

    assert response.status_code == 200
    updated = store.get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
    assert updated is not None
    assert updated["autopilot_pause_reason"] == "CUSTOMER"
    assert updated["channel_preferences"]["REDDIT"] == "OFF"


def test_customer_cannot_enable_auto_for_channel_without_customer_execution_path() -> None:
    client, preview = _registered_client()

    response = client.put(
        f"/customer/workspace/{preview.project_id}/channels",
        json={"channels": [{"platform": "REDDIT", "mode": "AUTO"}]},
    )

    assert response.status_code == 409
    assert "Autonomous execution is not available" in response.json()["detail"]
    current = client.get(f"/customer/workspace/{preview.project_id}/channels").json()
    assert next(item for item in current if item["platform"] == "REDDIT")["mode"] == "RESEARCH_ONLY"


def test_channel_view_exposes_per_platform_spend_customers_cac_revenue_and_roas(monkeypatch) -> None:
    client, preview = _registered_client()
    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
    assert project is not None
    project["product_id"] = str(uuid4())
    store.put(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id), project)

    analytics = SimpleNamespace(
        breakdowns=[
            SimpleNamespace(
                dimension="PLATFORM",
                key="REDDIT",
                experiment_count=3,
                spend=80.0,
                paid_users=4,
                revenue=240.0,
                cac=20.0,
                roas=3.0,
            )
        ]
    )
    monkeypatch.setattr(
        customer_channels_module.distribution_analytics_service,
        "product_analytics",
        lambda _product_id: analytics,
    )

    response = client.get(f"/customer/workspace/{preview.project_id}/channels")

    assert response.status_code == 200
    reddit = next(item for item in response.json() if item["platform"] == "REDDIT")
    assert reddit["experiment_count"] == 3
    assert reddit["spend_usd"] == 80.0
    assert reddit["paid_customers"] == 4
    assert reddit["cac_usd"] == 20.0
    assert reddit["revenue_usd"] == 240.0
    assert reddit["roas"] == 3.0
