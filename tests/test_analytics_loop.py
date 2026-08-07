from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.analytics_service import analytics_service
from app.channel_service import channel_service
from app.execution_service import execution_service
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.main import app
from app.product_intake import product_intake_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    channel_service.reset()
    growth_play_service.reset()
    execution_service.reset()
    analytics_service.reset()


def _build_growth_product() -> tuple[str, list[dict]]:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized readings available on demand.\n"
                "Market: US\n"
                "Goal: Acquire 100 paid users\n"
                "Budget: 500\n"
                "Max CAC: 5"
            ),
            "reference_links": ["https://example.com/oracle"],
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/channels/discover").status_code == 200
    plays = client.post(f"/v1/products/{product_id}/growth-plays/generate")
    assert plays.status_code == 200
    return product_id, plays.json()["plays"]


def _prepare_experiment(product_id: str, play: dict, run: bool = True) -> tuple[dict, dict]:
    approved = client.post(
        f"/v1/products/{product_id}/growth-plays/{play['id']}/approval",
        json={"status": "APPROVED"},
    )
    assert approved.status_code == 200
    prepared = client.post(
        f"/v1/products/{product_id}/growth-plays/{play['id']}/execution/prepare",
        json={"contact_email": "partner@example.com"},
    )
    assert prepared.status_code == 200
    package = prepared.json()
    assert client.post(f"/v1/execution-packages/{package['id']}/approve").status_code == 200
    if not run:
        experiment = client.get(f"/v1/experiments/{package['experiment_id']}").json()
        return package, experiment
    launched = client.post(f"/v1/execution-packages/{package['id']}/run")
    assert launched.status_code == 200
    return launched.json()["package"], launched.json()["experiment"]


def test_analytics_rejects_events_before_experiment_runs() -> None:
    product_id, plays = _build_growth_product()
    package, experiment = _prepare_experiment(product_id, plays[0], run=False)
    response = client.post(
        "/v1/analytics/events",
        json={
            "event_type": "VISIT",
            "experiment_id": experiment["id"],
            "referral_token": package["referral_token"],
        },
    )
    assert response.status_code == 409


def test_attribution_metrics_cac_and_idempotency() -> None:
    product_id, plays = _build_growth_product()
    package, experiment = _prepare_experiment(product_id, plays[0])
    event_id = str(uuid4())

    visit = client.post(
        "/v1/analytics/events",
        json={
            "event_id": event_id,
            "event_type": "VISIT",
            "referral_token": package["referral_token"],
            "actor_id": "user-1",
        },
    )
    assert visit.status_code == 202
    assert visit.json()["attributed_by"] == "referral_token"

    duplicate_visit = client.post(
        "/v1/analytics/events",
        json={
            "event_id": event_id,
            "event_type": "VISIT",
            "referral_token": package["referral_token"],
            "actor_id": "user-1",
        },
    )
    assert duplicate_visit.status_code == 202
    assert duplicate_visit.json()["duplicate"] is True

    signup = client.post(
        "/v1/analytics/events",
        json={
            "event_type": "SIGNUP",
            "utm_content": plays[0]["id"],
            "actor_id": "user-1",
        },
    )
    assert signup.status_code == 202
    assert signup.json()["attributed_by"] == "utm_content"

    activated = client.post(
        "/v1/analytics/events",
        json={
            "event_type": "ACTIVATED",
            "experiment_id": experiment["id"],
            "actor_id": "user-1",
        },
    )
    assert activated.status_code == 202

    paid_one = client.post(
        "/v1/analytics/events",
        json={
            "event_type": "PAID",
            "experiment_id": experiment["id"],
            "referral_token": package["referral_token"],
            "utm_content": plays[0]["id"],
            "actor_id": "user-1",
            "revenue": 9.99,
        },
    )
    assert paid_one.status_code == 202
    assert paid_one.json()["attributed_by"] == (
        "experiment_id+referral_token+utm_content"
    )

    paid_two = client.post(
        "/v1/analytics/events",
        json={
            "event_type": "PAID",
            "experiment_id": experiment["id"],
            "actor_id": "user-1",
            "revenue": 9.99,
        },
    )
    assert paid_two.status_code == 202

    spend_id = str(uuid4())
    spend = client.post(
        f"/v1/experiments/{experiment['id']}/spend",
        json={"spend_id": spend_id, "amount": 25},
    )
    assert spend.status_code == 202
    duplicate_spend = client.post(
        f"/v1/experiments/{experiment['id']}/spend",
        json={"spend_id": spend_id, "amount": 25},
    )
    assert duplicate_spend.status_code == 202
    assert duplicate_spend.json()["duplicate"] is True

    analytics = client.get(f"/v1/experiments/{experiment['id']}/analytics")
    assert analytics.status_code == 200
    body = analytics.json()
    metrics = body["metrics"]
    assert body["event_count"] == 5
    assert metrics["visits"] == 1
    assert metrics["signups"] == 1
    assert metrics["activated_users"] == 1
    assert metrics["paid_users"] == 1
    assert metrics["transactions"] == 2
    assert metrics["revenue"] == 19.98
    assert metrics["spend"] == 25.0
    assert metrics["visit_to_signup_rate"] == 1.0
    assert metrics["signup_to_paid_rate"] == 1.0
    assert metrics["cac"] == 25.0
    assert metrics["roas"] == 0.799
    assert metrics["revenue_per_paid_user"] == 19.98

    dashboard = client.get(f"/v1/products/{product_id}/analytics")
    assert dashboard.status_code == 200
    product_metrics = dashboard.json()
    assert product_metrics["experiment_count"] == 1
    assert product_metrics["total_spend"] == 25.0
    assert product_metrics["total_paid_users"] == 1
    assert product_metrics["total_revenue"] == 19.98
    assert product_metrics["blended_cac"] == 25.0
    assert product_metrics["blended_roas"] == 0.799


def test_conflicting_attribution_identifiers_are_rejected() -> None:
    product_id, plays = _build_growth_product()
    _, first_experiment = _prepare_experiment(product_id, plays[0])
    second_package, _ = _prepare_experiment(product_id, plays[1])

    response = client.post(
        "/v1/analytics/events",
        json={
            "event_type": "VISIT",
            "experiment_id": first_experiment["id"],
            "referral_token": second_package["referral_token"],
        },
    )
    assert response.status_code == 409
    assert "different experiments" in response.json()["detail"]


def test_reusing_event_id_for_different_event_is_rejected() -> None:
    product_id, plays = _build_growth_product()
    package, _ = _prepare_experiment(product_id, plays[0])
    event_id = str(uuid4())
    first = client.post(
        "/v1/analytics/events",
        json={
            "event_id": event_id,
            "event_type": "VISIT",
            "referral_token": package["referral_token"],
        },
    )
    assert first.status_code == 202
    conflict = client.post(
        "/v1/analytics/events",
        json={
            "event_id": event_id,
            "event_type": "SIGNUP",
            "referral_token": package["referral_token"],
        },
    )
    assert conflict.status_code == 409


def test_revenue_on_non_paid_event_is_validation_error() -> None:
    product_id, plays = _build_growth_product()
    package, _ = _prepare_experiment(product_id, plays[0])
    response = client.post(
        "/v1/analytics/events",
        json={
            "event_type": "SIGNUP",
            "referral_token": package["referral_token"],
            "revenue": 9.99,
        },
    )
    assert response.status_code == 422
