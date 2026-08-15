from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.distribution_event_ingestion import distribution_event_key_service
from app.main import app
from app.product_intake import product_intake_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state() -> None:
    app.dependency_overrides.pop(get_settings, None)
    product_intake_service.reset()
    distribution_event_key_service.reset()
    yield
    app.dependency_overrides.pop(get_settings, None)


def _product() -> str:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Integration Test\n"
                "Description: Digital subscription product.\n"
                "Problem: Users need a focused workflow.\n"
                "Value proposition: Faster task completion.\n"
                "Market: US\n"
                "Language: English\n"
                "Budget: 500\n"
                "Max CAC: 10\n"
                "Goal: Acquire 100 paid users"
            )
        },
    )
    assert response.status_code == 201
    return response.json()["product"]["id"]


def _override_settings(**overrides: object) -> None:
    settings = Settings(_env_file=None, **overrides)
    app.dependency_overrides[get_settings] = lambda: settings


def test_integration_status_exposes_actionable_configuration_blockers() -> None:
    product_id = _product()
    _override_settings(partizan_public_base_url=None)

    response = client.get(f"/v1/products/{product_id}/integration-status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["event_key_configured"] is False
    assert payload["public_tracking_configured"] is False
    assert payload["experiment_count"] == 0
    assert payload["ready_for_attributed_conversions"] is False
    assert payload["observed_event_types"] == []
    assert payload["unobserved_event_types"] == ["VISIT", "SIGNUP", "ACTIVATED", "PAID"]
    assert len(payload["blockers"]) == 3
    assert "event_key" not in payload


def test_integration_status_never_returns_event_key_plaintext() -> None:
    product_id = _product()
    created = client.post(f"/v1/products/{product_id}/distribution-event-key")
    assert created.status_code == 200
    plaintext = created.json()["event_key"]
    _override_settings(partizan_public_base_url="https://partizan.example.com")

    response = client.get(f"/v1/products/{product_id}/integration-status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["event_key_configured"] is True
    assert payload["public_tracking_configured"] is True
    assert payload["public_base_url"] == "https://partizan.example.com"
    assert payload["ready_for_attributed_conversions"] is False
    assert payload["blockers"] == [
        "Create a DistributionExperiment before verifying attributed events"
    ]
    assert plaintext not in response.text


def test_integration_status_surfaces_observed_real_funnel(monkeypatch: pytest.MonkeyPatch) -> None:
    product_id = _product()
    assert client.post(f"/v1/products/{product_id}/distribution-event-key").status_code == 200
    _override_settings(partizan_public_base_url="https://partizan.example.com")

    metrics = SimpleNamespace(visits=3, signups=2, activated_users=1, paid_users=1)
    analytics = SimpleNamespace(
        experiment_count=1,
        experiments=[SimpleNamespace(metrics=metrics)],
    )
    monkeypatch.setattr(
        "app.product_integration_status.distribution_analytics_service.product_analytics",
        lambda _product_id: analytics,
    )

    response = client.get(f"/v1/products/{product_id}/integration-status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready_for_attributed_conversions"] is True
    assert payload["funnel"] == {
        "visits": 3,
        "signups": 2,
        "activated_users": 1,
        "paid_users": 1,
    }
    assert payload["observed_event_types"] == ["VISIT", "SIGNUP", "ACTIVATED", "PAID"]
    assert payload["unobserved_event_types"] == []
    assert payload["blockers"] == []
