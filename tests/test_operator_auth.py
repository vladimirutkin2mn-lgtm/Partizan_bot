from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.operator_auth import OPERATOR_KEY_HEADER

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_settings_override() -> None:
    app.dependency_overrides.pop(get_settings, None)
    yield
    app.dependency_overrides.pop(get_settings, None)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def _override(settings: Settings) -> None:
    app.dependency_overrides[get_settings] = lambda: settings


def test_local_default_allows_operator_route_without_key() -> None:
    _override(_settings(app_env="local", operator_auth_required=False))

    response = client.get("/v1/ops/paid-control/sweeps?limit=1")

    assert response.status_code == 200


def test_production_without_configured_operator_key_fails_closed() -> None:
    _override(_settings(app_env="production", operator_api_key=None))

    response = client.get("/v1/ops/paid-control/sweeps?limit=1")

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_production_missing_or_wrong_operator_header_is_unauthorized() -> None:
    _override(_settings(app_env="production", operator_api_key="correct-secret"))

    missing = client.get("/v1/ops/paid-control/sweeps?limit=1")
    wrong = client.get(
        "/v1/ops/paid-control/sweeps?limit=1",
        headers={OPERATOR_KEY_HEADER: "wrong-secret"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert "correct-secret" not in missing.text
    assert "correct-secret" not in wrong.text


def test_correct_operator_key_allows_protected_route() -> None:
    _override(_settings(app_env="production", operator_api_key="correct-secret"))

    response = client.get(
        "/v1/ops/paid-control/sweeps?limit=1",
        headers={OPERATOR_KEY_HEADER: "correct-secret"},
    )

    assert response.status_code == 200


def test_explicit_operator_auth_can_be_required_outside_production() -> None:
    _override(
        _settings(
            app_env="staging",
            operator_auth_required=True,
            operator_api_key="staging-secret",
        )
    )

    missing = client.get("/v1/ops/paid-control/sweeps?limit=1")
    allowed = client.get(
        "/v1/ops/paid-control/sweeps?limit=1",
        headers={OPERATOR_KEY_HEADER: "staging-secret"},
    )

    assert missing.status_code == 401
    assert allowed.status_code == 200


def test_generic_execute_is_protected_before_action_lookup() -> None:
    _override(_settings(app_env="production", operator_api_key="correct-secret"))
    action_id = uuid4()

    blocked = client.post(f"/v1/distribution-actions/{action_id}/execute", json={})
    allowed_to_lookup = client.post(
        f"/v1/distribution-actions/{action_id}/execute",
        json={},
        headers={OPERATOR_KEY_HEADER: "correct-secret"},
    )

    assert blocked.status_code == 401
    assert allowed_to_lookup.status_code == 404


def test_distribution_action_read_remains_outside_operator_boundary() -> None:
    _override(_settings(app_env="production", operator_api_key="correct-secret"))

    response = client.get(f"/v1/distribution-actions/{uuid4()}")

    assert response.status_code == 404
