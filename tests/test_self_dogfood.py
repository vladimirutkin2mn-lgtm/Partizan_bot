from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.config import get_settings
from app.customer_account import CUSTOMER_ACCOUNT_SESSION_COOKIE, customer_account_service
from app.customer_funnel import customer_funnel_service
from app.distribution_analytics_schemas import DistributionSpendCreate
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_service import distribution_play_service
from app.icp_service import icp_service
from app.main import app
from app.product_intake import product_intake_service
from app.self_dogfood import (
    SELF_DOGFOOD_ATTRIBUTION_COOKIE,
    self_dogfood_service,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARTIZAN_SELF_DOGFOOD_PRODUCT_ID", raising=False)
    monkeypatch.delenv("PARTIZAN_PUBLIC_BASE_URL", raising=False)
    get_settings.cache_clear()
    product_intake_service.reset()
    icp_service.reset()
    audience_intelligence_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    distribution_analytics_service.reset()
    distribution_growth_manager_service.reset()
    customer_funnel_service.reset()
    customer_account_service.reset()
    self_dogfood_service.reset()
    client.cookies.clear()
    yield
    client.cookies.clear()
    monkeypatch.delenv("PARTIZAN_SELF_DOGFOOD_PRODUCT_ID", raising=False)
    monkeypatch.delenv("PARTIZAN_PUBLIC_BASE_URL", raising=False)
    get_settings.cache_clear()


def _partizan_product() -> str:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Partizan\n"
                "Description: AI customer acquisition system for founders and small teams.\n"
                "Problem: Founders do not know where to spend a small acquisition budget.\n"
                "Value proposition: Find, test and learn across acquisition channels.\n"
                "Market: United States\n"
                "Language: English\n"
                "Budget: 1000\n"
                "Max CAC: 30\n"
                "Goal: Acquire paying customers"
            )
        },
    )
    assert response.status_code == 201
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution-plays/generate").status_code == 200
    return product_id


def _running_self_dogfood_experiment(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, dict]:
    product_id = _partizan_product()
    monkeypatch.setenv("PARTIZAN_PUBLIC_BASE_URL", "https://partizan.example")
    monkeypatch.setenv("PARTIZAN_SELF_DOGFOOD_PRODUCT_ID", product_id)
    get_settings.cache_clear()

    plays = client.get(f"/v1/products/{product_id}/distribution-plays").json()["plays"]
    play = next(item for item in plays if item["tactic_id"] == "instagram_ads")
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://partizan.example/start"},
    )
    assert prepared.status_code == 200
    plan = prepared.json()
    action_id = plan["action"]["id"]
    assert client.post(f"/v1/distribution-actions/{action_id}/approve").status_code == 200
    running = client.post(
        f"/v1/distribution-actions/{action_id}/mark-executed",
        json={"external_reference": "self-dogfood-test"},
    )
    assert running.status_code == 200
    return product_id, running.json()


def _click_self_dogfood(experiment: dict) -> None:
    response = client.get(
        f"/r/{experiment['referral_token']}",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://partizan.example/start?")
    assert SELF_DOGFOOD_ATTRIBUTION_COOKIE in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


def _create_bound_preview() -> dict:
    response = client.post(
        "/v1/customer-projects/preview",
        json={
            "brief": (
                "AI bookkeeping assistant for independent founders that categorizes expenses, "
                "reconciles transactions, and prepares monthly financial summaries."
            ),
            "goal": "Get paying customers",
            "budget_usd": 1000,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_tracking_redirect_captures_first_party_referral_only_for_configured_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product_id, running = _running_self_dogfood_experiment(monkeypatch)
    experiment = running["experiment"]

    _click_self_dogfood(experiment)
    assert client.cookies.get(SELF_DOGFOOD_ATTRIBUTION_COOKIE) == experiment["referral_token"]

    second = client.get(f"/r/{experiment['referral_token']}", follow_redirects=False)
    assert second.status_code == 302
    assert SELF_DOGFOOD_ATTRIBUTION_COOKIE not in second.headers.get("set-cookie", "")

    monkeypatch.setenv(
        "PARTIZAN_SELF_DOGFOOD_PRODUCT_ID",
        "11111111-1111-1111-1111-111111111111",
    )
    get_settings.cache_clear()
    client.cookies.clear()
    mismatch = client.get(f"/r/{experiment['referral_token']}", follow_redirects=False)
    assert mismatch.status_code == 302
    assert SELF_DOGFOOD_ATTRIBUTION_COOKIE not in mismatch.headers.get("set-cookie", "")
    assert product_id != "11111111-1111-1111-1111-111111111111"


def test_self_dogfood_records_signup_activation_and_paid_purchase_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product_id, running = _running_self_dogfood_experiment(monkeypatch)
    experiment = running["experiment"]
    _click_self_dogfood(experiment)
    preview = _create_bound_preview()
    project_id = UUID(preview["project_id"])

    binding = self_dogfood_service.project_binding(project_id)
    assert binding is not None
    assert str(binding.product_id) == product_id
    assert str(binding.experiment_id) == experiment["id"]

    registered = client.post(
        "/customer/account/register",
        json={
            "email": "self-dogfood@example.com",
            "password": "correct-horse-42",
            "project_id": preview["project_id"],
            "customer_token": preview["customer_token"],
        },
    )
    assert registered.status_code == 200

    workspace = client.get(f"/customer/workspace/{preview['project_id']}")
    assert workspace.status_code == 200
    workspace_repeat = client.get(f"/customer/workspace/{preview['project_id']}")
    assert workspace_repeat.status_code == 200

    session_token = client.cookies.get(CUSTOMER_ACCOUNT_SESSION_COOKIE)
    assert session_token
    _, current_customer_token = customer_account_service.project_access(
        session_token=session_token,
        project_id=project_id,
    )
    customer_funnel_service.mark_checkout_pending(
        project_id,
        current_customer_token,
        "cs_self_dogfood",
    )

    session = {
        "id": "cs_self_dogfood",
        "payment_status": "paid",
        "client_reference_id": preview["project_id"],
        "customer": "cus_self_dogfood",
        "amount_total": 4900,
        "metadata": {
            "partizan_project_id": preview["project_id"],
            "partizan_entitlement": "launch_plan",
        },
    }
    monkeypatch.setattr("app.customer_routes.retrieve_launch_checkout", lambda **kwargs: session)

    paid = client.post(
        f"/v1/customer-projects/{preview['project_id']}/recover-access",
        json={"session_id": "cs_self_dogfood"},
    )
    assert paid.status_code == 200
    paid_repeat = client.post(
        f"/v1/customer-projects/{preview['project_id']}/recover-access",
        json={"session_id": "cs_self_dogfood"},
    )
    assert paid_repeat.status_code == 200

    analytics = distribution_analytics_service.experiment_analytics(UUID(experiment["id"]))
    assert analytics.metrics.visits == 1
    assert analytics.metrics.signups == 1
    assert analytics.metrics.activated_users == 1
    assert analytics.metrics.paid_users == 1
    assert analytics.metrics.revenue == 49


def test_self_dogfood_snapshot_turns_green_only_after_real_funnel_economics_and_learning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product_id, running = _running_self_dogfood_experiment(monkeypatch)
    experiment = running["experiment"]
    _click_self_dogfood(experiment)
    preview = _create_bound_preview()
    project_id = UUID(preview["project_id"])
    settings = get_settings()

    self_dogfood_service.record_project_event(
        project_id,
        event_type="SIGNUP",
        business_key="account:test",
        settings=settings,
    )
    self_dogfood_service.record_project_event(
        project_id,
        event_type="ACTIVATED",
        business_key="workspace",
        settings=settings,
    )
    self_dogfood_service.record_project_event(
        project_id,
        event_type="PAID",
        business_key="stripe:test",
        settings=settings,
        revenue=49,
    )

    before = self_dogfood_service.snapshot(settings)
    assert before.proof_ready is False
    assert "Record real experiment spend before CAC can be calculated" in before.blockers
    assert "Run Growth Manager after real economics arrive" in before.blockers

    experiment_id = UUID(experiment["id"])
    distribution_analytics_service.add_spend(
        experiment_id,
        DistributionSpendCreate(amount=20, properties={"source": "test-reconciliation"}),
    )
    decision = distribution_growth_manager_service.evaluate(experiment_id)
    assert decision.action in {"SCALE", "CONTINUE", "MODIFY", "STOP"}

    after = self_dogfood_service.snapshot(settings)
    assert after.configured_product_id == UUID(product_id)
    assert after.visits == 1
    assert after.signups == 1
    assert after.activated_users == 1
    assert after.paid_users == 1
    assert after.spend_usd == 20
    assert after.revenue_usd == 49
    assert after.cac_usd == 20
    assert after.learning_entries == 1
    assert after.latest_decision == decision.action
    assert after.blockers == []
    assert after.proof_ready is True


def test_self_dogfood_snapshot_is_read_only_and_fail_closed_when_not_configured() -> None:
    snapshot = self_dogfood_service.snapshot(get_settings())

    assert snapshot.configured_product_id is None
    assert snapshot.proof_ready is False
    assert snapshot.blockers == ["PARTIZAN_SELF_DOGFOOD_PRODUCT_ID is not configured"]
