from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.operator_auth import (
    OPERATOR_KEY_HEADER,
    PUBLIC_MUTATION_ROUTE_TEMPLATES,
    require_control_plane_operator,
)

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


def test_global_control_plane_guard_is_installed() -> None:
    dependency_calls = {dependency.dependency for dependency in app.router.dependencies}

    assert require_control_plane_operator in dependency_calls
    assert PUBLIC_MUTATION_ROUTE_TEMPLATES == {
        ("POST", "/v1/products/{product_id}/distribution-events"),
        ("POST", "/v1/products/{product_id}/distribution-events/verify"),
    }


def test_local_default_allows_operator_route_without_key() -> None:
    _override(_settings(app_env="local", operator_auth_required=False))

    response = client.get("/v1/ops/paid-control/sweeps?limit=1")

    assert response.status_code == 200


def test_local_default_keeps_legacy_mutation_compatible() -> None:
    _override(_settings(app_env="local", operator_auth_required=False))

    response = client.post("/v1/products", json={"brief": "A small test product"})

    assert response.status_code == 201


def test_production_without_configured_operator_key_fails_closed() -> None:
    _override(_settings(app_env="production", operator_api_key=None))

    protected_get = client.get("/v1/ops/paid-control/sweeps?limit=1")
    protected_mutation = client.post("/v1/products", json={"brief": "Blocked"})

    assert protected_get.status_code == 503
    assert protected_mutation.status_code == 503
    assert "not configured" in protected_get.json()["detail"]
    assert "not configured" in protected_mutation.json()["detail"]


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
    mutation_missing = client.post("/v1/products", json={"brief": "Blocked"})

    assert missing.status_code == 401
    assert allowed.status_code == 200
    assert mutation_missing.status_code == 401


def test_global_guard_blocks_legacy_provider_execution_before_lookup() -> None:
    _override(_settings(app_env="production", operator_api_key="correct-secret"))
    package_id = uuid4()

    blocked = client.post(f"/v1/execution-packages/{package_id}/run")
    allowed_to_lookup = client.post(
        f"/v1/execution-packages/{package_id}/run",
        headers={OPERATOR_KEY_HEADER: "correct-secret"},
    )

    assert blocked.status_code == 401
    assert allowed_to_lookup.status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/v1/products", {"brief": "Blocked"}),
        ("POST", f"/v1/products/{uuid4()}/icps/generate", None),
        ("POST", f"/v1/products/{uuid4()}/distribution/discover", None),
        ("POST", f"/v1/distribution-experiments/{uuid4()}/growth-decision", None),
        ("POST", f"/v1/distribution-experiments/{uuid4()}/finish", None),
        ("PATCH", f"/v1/execution-packages/{uuid4()}", {"subject": "x", "body": "y"}),
    ],
)
def test_global_guard_blocks_control_plane_mutations(method: str, path: str, body) -> None:
    _override(_settings(app_env="production", operator_api_key="correct-secret"))

    response = client.request(method, path, json=body)

    assert response.status_code == 401
    assert response.json()["detail"] == "Operator authentication required"


def test_event_key_data_plane_remains_outside_operator_boundary() -> None:
    _override(_settings(app_env="production", operator_api_key="correct-secret"))
    product_id = uuid4()
    payload = {
        "event_id": str(uuid4()),
        "event_type": "VISIT",
        "experiment_id": str(uuid4()),
        "actor_id": "public-data-plane-test",
    }

    ingest = client.post(f"/v1/products/{product_id}/distribution-events", json=payload)
    verify = client.post(
        f"/v1/products/{product_id}/distribution-events/verify",
        json=payload,
    )

    assert ingest.status_code == 401
    assert verify.status_code == 401
    assert ingest.json()["detail"] == "Distribution event authentication required"
    assert verify.json()["detail"] == "Distribution event authentication required"


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
