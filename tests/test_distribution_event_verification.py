from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.config import get_settings
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_event_ingestion import (
    DISTRIBUTION_EVENT_KEY_HEADER,
    distribution_event_key_service,
)
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_service import distribution_play_service
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.main import app
from app.product_intake import product_intake_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state() -> None:
    app.dependency_overrides.pop(get_settings, None)
    product_intake_service.reset()
    icp_service.reset()
    audience_intelligence_service.reset()
    growth_play_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    distribution_analytics_service.reset()
    distribution_growth_manager_service.reset()
    distribution_event_key_service.reset()
    yield
    app.dependency_overrides.pop(get_settings, None)


def _product(name: str) -> str:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                f"Product: {name}\n"
                "Description: Digital subscription product with personalized insights.\n"
                "Problem: Users want fast personalized guidance.\n"
                "Value proposition: Personalized answers available on demand.\n"
                "Market: US\n"
                "Language: English\n"
                "Budget: 500\n"
                "Max CAC: 10\n"
                "Goal: Acquire 100 paid users"
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


def _prepared_experiment(product_id: str, tactic_id: str = "instagram_ads") -> dict:
    plays = client.get(f"/v1/products/{product_id}/distribution-plays")
    assert plays.status_code == 200
    play = next(item for item in plays.json()["plays"] if item["tactic_id"] == tactic_id)
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/product"},
    )
    assert prepared.status_code == 200
    return prepared.json()["experiment"]


def _running_experiment(product_id: str, tactic_id: str = "instagram_ads") -> dict:
    experiment = _prepared_experiment(product_id, tactic_id)
    action_id = experiment["action_id"]
    assert client.post(f"/v1/distribution-actions/{action_id}/approve").status_code == 200
    running = client.post(
        f"/v1/distribution-actions/{action_id}/mark-executed",
        json={"external_reference": f"campaign-{uuid4()}"},
    )
    assert running.status_code == 200
    return running.json()["experiment"]


def _event_key(product_id: str) -> str:
    response = client.post(f"/v1/products/{product_id}/distribution-event-key")
    assert response.status_code == 200
    return response.json()["event_key"]


def _verify(product_id: str, event_key: str, payload: dict):
    return client.post(
        f"/v1/products/{product_id}/distribution-events/verify",
        json=payload,
        headers={DISTRIBUTION_EVENT_KEY_HEADER: event_key},
    )


def test_valid_paid_verification_does_not_persist_or_change_economics() -> None:
    product_id = _product("Verification")
    experiment = _running_experiment(product_id)
    event_key = _event_key(product_id)
    event_id = str(uuid4())

    response = _verify(
        product_id,
        event_key,
        {
            "event_id": event_id,
            "event_type": "PAID",
            "experiment_id": experiment["id"],
            "actor_id": "verification-user",
            "revenue": 19.9,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "persisted": False,
        "event_id": event_id,
        "experiment_id": experiment["id"],
        "event_type": "PAID",
        "attributed_by": "experiment_id",
        "duplicate": False,
        "detail": "Event is valid and was not persisted",
    }

    analytics = client.get(
        f"/v1/distribution-experiments/{experiment['id']}/analytics"
    )
    assert analytics.status_code == 200
    assert analytics.json()["event_count"] == 0
    assert analytics.json()["metrics"]["paid_users"] == 0
    assert analytics.json()["metrics"]["revenue"] == 0


def test_verification_requires_the_real_product_event_key() -> None:
    product_id = _product("Verification Auth")
    experiment = _running_experiment(product_id)

    response = _verify(
        product_id,
        "ptz_evt_wrong",
        {"event_type": "SIGNUP", "experiment_id": experiment["id"]},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "PartizanEventKey"


def test_verification_is_product_bound_and_still_does_not_persist() -> None:
    product_a = _product("Verification A")
    product_b = _product("Verification B")
    experiment_b = _running_experiment(product_b, "tiktok_ads")
    key_a = _event_key(product_a)

    response = _verify(
        product_a,
        key_a,
        {
            "event_type": "PAID",
            "experiment_id": experiment_b["id"],
            "actor_id": "wrong-product",
            "revenue": 999,
        },
    )

    assert response.status_code == 403
    analytics = client.get(
        f"/v1/distribution-experiments/{experiment_b['id']}/analytics"
    )
    assert analytics.status_code == 200
    assert analytics.json()["event_count"] == 0


def test_verification_rejects_non_measurable_experiment_without_mutation() -> None:
    product_id = _product("Verification Draft")
    experiment = _prepared_experiment(product_id)
    event_key = _event_key(product_id)

    response = _verify(
        product_id,
        event_key,
        {"event_type": "SIGNUP", "experiment_id": experiment["id"]},
    )

    assert response.status_code == 409
    assert "RUNNING or FINISHED" in response.json()["detail"]


def test_verification_reports_existing_identical_event_as_duplicate_without_rewrite() -> None:
    product_id = _product("Verification Duplicate")
    experiment = _running_experiment(product_id)
    event_key = _event_key(product_id)
    event_id = str(uuid4())
    payload = {
        "event_id": event_id,
        "event_type": "SIGNUP",
        "experiment_id": experiment["id"],
        "actor_id": "existing-user",
    }

    ingested = client.post(
        f"/v1/products/{product_id}/distribution-events",
        json=payload,
        headers={DISTRIBUTION_EVENT_KEY_HEADER: event_key},
    )
    assert ingested.status_code == 201

    verified = _verify(product_id, event_key, payload)
    assert verified.status_code == 200
    assert verified.json()["duplicate"] is True
    assert verified.json()["persisted"] is False

    analytics = client.get(
        f"/v1/distribution-experiments/{experiment['id']}/analytics"
    )
    assert analytics.status_code == 200
    assert analytics.json()["event_count"] == 1
    assert analytics.json()["metrics"]["signups"] == 1
