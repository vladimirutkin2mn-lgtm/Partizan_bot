from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.config import Settings, get_settings
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_event_ingestion import (
    DISTRIBUTION_EVENT_KEY_HEADER,
    DISTRIBUTION_EVENT_KEY_NAMESPACE,
    DistributionEventKeyService,
    distribution_event_key_service,
)
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_service import distribution_play_service
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.main import app
from app.operator_auth import OPERATOR_KEY_HEADER
from app.product_intake import product_intake_service
from app.runtime_store import MemoryRuntimeStateStore

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


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def _override(settings: Settings) -> None:
    app.dependency_overrides[get_settings] = lambda: settings


def _product(name: str) -> str:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                f"Product: {name}\n"
                "Description: AI entertainment product with personalized insights.\n"
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


def _running_experiment(product_id: str, tactic_id: str = "instagram_ads") -> dict:
    plays = client.get(f"/v1/products/{product_id}/distribution-plays")
    assert plays.status_code == 200
    play = next(item for item in plays.json()["plays"] if item["tactic_id"] == tactic_id)
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/product"},
    )
    assert prepared.status_code == 200
    action_id = prepared.json()["action"]["id"]
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
    payload = response.json()
    assert payload["configured"] is True
    assert payload["event_key"].startswith("ptz_evt_")
    return payload["event_key"]


def _ingest(product_id: str, event_key: str, payload: dict):
    return client.post(
        f"/v1/products/{product_id}/distribution-events",
        json=payload,
        headers={DISTRIBUTION_EVENT_KEY_HEADER: event_key},
    )


def test_key_plaintext_is_returned_once_and_only_digest_is_persisted() -> None:
    store = MemoryRuntimeStateStore()
    service = DistributionEventKeyService(store)
    product_id = uuid4()

    created = service.rotate(product_id)
    status = service.status(product_id)
    stored = store.get(DISTRIBUTION_EVENT_KEY_NAMESPACE, str(product_id))

    assert created.event_key.startswith("ptz_evt_")
    assert created.event_key not in str(stored)
    assert stored is not None
    assert stored["key_digest"]
    assert len(stored["key_digest"]) == 64
    assert status.configured is True
    assert "event_key" not in status.model_dump()
    assert status.key_hint == created.key_hint
    assert created.event_key not in (status.key_hint or "")


def test_rotation_invalidates_previous_key_and_status_never_returns_plaintext() -> None:
    product_id = _product("Oracle Rotation")
    first = _event_key(product_id)
    second = _event_key(product_id)

    assert first != second
    assert distribution_event_key_service.verify(uuid4_from(product_id), first) is False
    assert distribution_event_key_service.verify(uuid4_from(product_id), second) is True

    status = client.get(f"/v1/products/{product_id}/distribution-event-key")
    assert status.status_code == 200
    assert status.json()["configured"] is True
    assert "event_key" not in status.json()
    assert first not in status.text
    assert second not in status.text


def test_revoke_invalidates_key() -> None:
    product_id = _product("Oracle Revoke")
    event_key = _event_key(product_id)

    revoked = client.delete(f"/v1/products/{product_id}/distribution-event-key")

    assert revoked.status_code == 200
    assert revoked.json()["configured"] is False
    assert distribution_event_key_service.verify(uuid4_from(product_id), event_key) is False


def test_missing_or_wrong_event_key_is_rejected_before_attribution_lookup() -> None:
    product_id = _product("Oracle Auth")
    experiment_id = str(uuid4())

    missing = client.post(
        f"/v1/products/{product_id}/distribution-events",
        json={"event_type": "SIGNUP", "experiment_id": experiment_id},
    )
    wrong = _ingest(
        product_id,
        "ptz_evt_wrong-key",
        {"event_type": "SIGNUP", "experiment_id": experiment_id},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.headers["www-authenticate"] == "PartizanEventKey"
    assert experiment_id not in missing.text
    assert experiment_id not in wrong.text


def test_valid_product_key_accepts_idempotent_paid_event() -> None:
    product_id = _product("Oracle Events")
    experiment = _running_experiment(product_id)
    event_key = _event_key(product_id)
    event_id = str(uuid4())
    payload = {
        "event_id": event_id,
        "event_type": "PAID",
        "experiment_id": experiment["id"],
        "actor_id": "customer-42",
        "revenue": 19.9,
    }

    first = _ingest(product_id, event_key, payload)
    duplicate = _ingest(product_id, event_key, payload)

    assert first.status_code == 201
    assert first.json()["duplicate"] is False
    assert duplicate.status_code == 201
    assert duplicate.json()["duplicate"] is True

    analytics = client.get(
        f"/v1/distribution-experiments/{experiment['id']}/analytics"
    )
    assert analytics.status_code == 200
    assert analytics.json()["metrics"]["paid_users"] == 1
    assert analytics.json()["metrics"]["revenue"] == 19.9


def test_product_key_cannot_write_to_another_products_experiment() -> None:
    product_a = _product("Oracle A")
    product_b = _product("Oracle B")
    experiment_b = _running_experiment(product_b, "tiktok_ads")
    key_a = _event_key(product_a)
    event_id = str(uuid4())

    blocked = _ingest(
        product_a,
        key_a,
        {
            "event_id": event_id,
            "event_type": "PAID",
            "experiment_id": experiment_b["id"],
            "actor_id": "forged-user",
            "revenue": 999,
        },
    )

    assert blocked.status_code == 403
    assert "another product" in blocked.json()["detail"]
    analytics = client.get(
        f"/v1/distribution-experiments/{experiment_b['id']}/analytics"
    )
    assert analytics.status_code == 200
    assert analytics.json()["event_count"] == 0
    assert analytics.json()["metrics"]["revenue"] == 0


def test_referral_token_is_accepted_but_still_product_bound() -> None:
    product_id = _product("Oracle Referral")
    experiment = _running_experiment(product_id)
    event_key = _event_key(product_id)

    response = _ingest(
        product_id,
        event_key,
        {
            "event_type": "SIGNUP",
            "referral_token": experiment["referral_token"],
            "actor_id": "signup-1",
        },
    )

    assert response.status_code == 201
    assert response.json()["experiment_id"] == experiment["id"]
    assert response.json()["attributed_by"] == "referral_token"


def test_generic_distribution_writes_require_operator_auth_in_production() -> None:
    _override(_settings(app_env="production", operator_api_key="operator-secret"))
    fake_experiment = str(uuid4())

    event_blocked = client.post(
        "/v1/distribution-analytics/events",
        json={"event_type": "VISIT", "experiment_id": fake_experiment},
    )
    spend_blocked = client.post(
        f"/v1/distribution-experiments/{fake_experiment}/spend",
        json={"amount": 10},
    )
    finish_blocked = client.post(
        f"/v1/distribution-experiments/{fake_experiment}/finish"
    )

    assert event_blocked.status_code == 401
    assert spend_blocked.status_code == 401
    assert finish_blocked.status_code == 401

    allowed_to_lookup = client.post(
        "/v1/distribution-analytics/events",
        json={"event_type": "VISIT", "experiment_id": fake_experiment},
        headers={OPERATOR_KEY_HEADER: "operator-secret"},
    )
    assert allowed_to_lookup.status_code == 404


def test_event_key_management_requires_operator_auth_in_production() -> None:
    product_id = _product("Oracle Protected Key")
    _override(_settings(app_env="production", operator_api_key="operator-secret"))

    missing = client.post(f"/v1/products/{product_id}/distribution-event-key")
    allowed = client.post(
        f"/v1/products/{product_id}/distribution-event-key",
        headers={OPERATOR_KEY_HEADER: "operator-secret"},
    )

    assert missing.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["event_key"].startswith("ptz_evt_")


def uuid4_from(value: str):
    from uuid import UUID

    return UUID(value)
