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
                "Product: Guide Test\n"
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


def test_guide_is_tied_to_product_and_public_base_url() -> None:
    product_id = _product()
    _override_settings(partizan_public_base_url="https://partizan.example.com")

    response = client.get(f"/v1/products/{product_id}/integration-guide")

    assert response.status_code == 200
    payload = response.json()
    expected_event = f"https://partizan.example.com/v1/products/{product_id}/distribution-events"
    assert payload["product_id"] == product_id
    assert payload["base_url"] == "https://partizan.example.com"
    assert payload["public_base_configured"] is True
    assert payload["event_endpoint"] == expected_event
    assert payload["verification_endpoint"] == f"{expected_event}/verify"
    assert payload["event_key_header"] == "X-Partizan-Event-Key"
    assert payload["attribution_fields"] == ["experiment_id", "action_id", "referral_token"]
    assert payload["event_types"] == ["VISIT", "SIGNUP", "ACTIVATED", "PAID"]

    snippets = payload["snippets"]
    for snippet in snippets.values():
        assert product_id in snippet
        assert "PARTIZAN_EVENT_KEY" in snippet
        assert "ptz_evt_" not in snippet


def test_guide_uses_safe_placeholder_when_public_base_is_not_configured() -> None:
    product_id = _product()
    _override_settings(partizan_public_base_url=None)

    response = client.get(f"/v1/products/{product_id}/integration-guide")

    assert response.status_code == 200
    payload = response.json()
    assert payload["base_url"] == "https://YOUR_PARTIZAN_HOST"
    assert payload["public_base_configured"] is False
    assert payload["event_endpoint"].startswith("https://YOUR_PARTIZAN_HOST/")
    assert payload["event_key_placeholder"] == "<PARTIZAN_EVENT_KEY>"


def test_guide_never_exposes_the_current_plaintext_event_key() -> None:
    product_id = _product()
    created = client.post(f"/v1/products/{product_id}/distribution-event-key")
    assert created.status_code == 200
    plaintext = created.json()["event_key"]
    _override_settings(partizan_public_base_url="https://partizan.example.com")

    response = client.get(f"/v1/products/{product_id}/integration-guide")

    assert response.status_code == 200
    assert plaintext not in response.text
    assert "<PARTIZAN_EVENT_KEY>" in response.text


def test_guide_encourages_stable_outbox_idempotency() -> None:
    product_id = _product()
    _override_settings(partizan_public_base_url="https://partizan.example.com")

    payload = client.get(f"/v1/products/{product_id}/integration-guide").json()

    guidance = " ".join(payload["outbox_guidance"]).lower()
    assert "stable event_id" in guidance
    assert "retry" in guidance
    assert "duplicate=true" in guidance
    assert "fresh event_id" in guidance
    assert "/verify" in payload["snippets"]["curl"]
