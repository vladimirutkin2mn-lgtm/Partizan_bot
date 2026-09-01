from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.operator_auth import (
    OPERATOR_KEY_HEADER,
    PUBLIC_API_ROUTE_TEMPLATES,
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
    assert PUBLIC_API_ROUTE_TEMPLATES == {
        ("POST", "/v1/products/{product_id}/distribution-events"),
        ("POST", "/v1/products/{product_id}/distribution-events/verify"),
        ("GET", "/v1/public/creative-blobs/{blob_id}"),
        ("POST", "/v1/customer-projects/preview"),
        ("POST", "/v1/customer-projects/{project_id}/product-clarification"),
        ("POST", "/v1/customer-projects/{project_id}/confirm-preview"),
        ("POST", "/v1/customer-projects/{project_id}/preview-research"),
        ("GET", "/v1/customer-projects/{project_id}"),
        ("POST", "/v1/customer-projects/{project_id}/checkout"),
        ("POST", "/v1/customer-projects/{project_id}/recover-access"),
        ("POST", "/v1/customer-projects/{project_id}/deep-research"),
        ("POST", "/v1/customer-projects/{project_id}/clarifications"),
        ("POST", "/v1/customer-projects/{project_id}/growth-balance/checkout"),
        ("POST", "/v1/customer-projects/{project_id}/growth-balance/verify"),
        ("PUT", "/v1/customer-projects/{project_id}/autopilot"),
        ("GET", "/v1/customer-projects/{project_id}/autopilot"),
        ("POST", "/v1/customer-projects/{project_id}/autopilot/status"),
        ("POST", "/v1/customer-projects/{project_id}/autopilot/meta/connect"),
        ("GET", "/v1/customer-projects/{project_id}/autopilot/meta/options"),
        ("POST", "/v1/customer-projects/{project_id}/autopilot/meta/connection"),
        ("GET", "/v1/customer-meta/oauth/callback"),
        ("POST", "/v1/billing/stripe/webhook"),
        ("POST", "/v1/billing/stripe/issuing-authorizations"),
        ("POST", "/v1/billing/stripe/issuing-events"),
    }


def test_local_default_allows_operator_route_without_key() -> None:
    _override(_settings(app_env="local", operator_auth_required=False))
    response = client.get("/v1/ops/paid-control/sweeps?limit=1")
    assert response.status_code == 200


def test_local_default_allows_control_plane_mutation_without_key() -> None:
    _override(_settings(app_env="local", operator_auth_required=False))
    response = client.post("/v1/products", json={"brief": "A small test product"})
    assert response.status_code == 201


def test_health_and_browser_surface_remain_public_in_production() -> None:
    _override(_settings(app_env="production", operator_api_key="correct-secret"))
    assert client.get("/health/ready").status_code == 200
    assert client.get("/app").status_code == 200


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


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/v1/products", {"brief": "Blocked"}),
        ("POST", f"/v1/products/{uuid4()}/icps/generate", None),
        ("POST", f"/v1/products/{uuid4()}/distribution/discover", None),
        ("POST", f"/v1/distribution-experiments/{uuid4()}/growth-decision", None),
        ("POST", f"/v1/distribution-experiments/{uuid4()}/finish", None),
        ("GET", f"/v1/distribution-actions/{uuid4()}", None),
        ("GET", f"/v1/products/{uuid4()}/distribution-analytics", None),
    ],
)
def test_global_guard_blocks_internal_control_plane_requests(method: str, path: str, body) -> None:
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
    verify = client.post(f"/v1/products/{product_id}/distribution-events/verify", json=payload)
    assert ingest.status_code == 401
    assert verify.status_code == 401
    assert ingest.json()["detail"] == "Distribution event authentication required"
    assert verify.json()["detail"] == "Distribution event authentication required"


def test_customer_boundary_bypasses_operator_key_but_keeps_customer_token_auth() -> None:
    _override(_settings(app_env="production", operator_api_key="correct-secret"))
    preview = client.post(
        "/v1/customer-projects/preview",
        json={
            "brief": "AI bookkeeping assistant for freelancers with a monthly subscription.",
            "market": "United States",
            "goal": "Get paying customers",
            "budget_usd": 1000,
        },
    )
    assert preview.status_code == 201
    project_id = preview.json()["project_id"]
    missing_customer_token = client.get(f"/v1/customer-projects/{project_id}")
    allowed = client.get(
        f"/v1/customer-projects/{project_id}",
        headers={"X-Partizan-Customer-Token": preview.json()["customer_token"]},
    )
    autopilot_without_customer_token = client.get(f"/v1/customer-projects/{project_id}/autopilot")
    growth_balance_without_customer_token = client.post(
        f"/v1/customer-projects/{project_id}/growth-balance/checkout",
        json={"amount_usd": 1000},
    )
    recovery_without_billing = client.post(
        f"/v1/customer-projects/{project_id}/recover-access",
        json={"session_id": "cs_test_missing"},
    )
    assert missing_customer_token.status_code == 401
    assert missing_customer_token.json()["detail"] == "Customer project token required"
    assert allowed.status_code == 200
    assert autopilot_without_customer_token.status_code == 401
    assert autopilot_without_customer_token.json()["detail"] == "Customer project token required"
    assert growth_balance_without_customer_token.status_code == 401
    assert growth_balance_without_customer_token.json()["detail"] == "Customer project token required"
    assert recovery_without_billing.status_code == 503


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            lambda project_id: f"/v1/customer-projects/{project_id}/product-clarification",
            {"answer": "It helps users automate bookkeeping."},
        ),
        (
            lambda project_id: f"/v1/customer-projects/{project_id}/confirm-preview",
            {
                "product": "AI bookkeeping assistant",
                "for_whom": "Automates bookkeeping and tax admin.",
                "likely_customer": "Independent freelancers",
                "likely_first_audiences": ["Independent freelancers"],
                "market": "United States",
                "goal": "Get first users",
                "budget_usd": 10,
            },
        ),
        (
            lambda project_id: f"/v1/customer-projects/{project_id}/preview-research",
            None,
        ),
    ],
)
def test_new_onboarding_steps_bypass_operator_auth_but_require_customer_token(path, body) -> None:
    _override(_settings(app_env="production", operator_api_key="correct-secret"))
    project_id = uuid4()

    response = client.post(path(project_id), json=body)

    assert response.status_code == 401
    assert response.json()["detail"] == "Customer project token required"


def test_public_creative_blob_route_bypasses_operator_boundary() -> None:
    _override(_settings(app_env="production", operator_api_key="correct-secret"))
    response = client.get(f"/v1/public/creative-blobs/{uuid4()}")
    assert response.status_code == 404


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


def test_distribution_action_read_requires_operator_in_production() -> None:
    _override(_settings(app_env="production", operator_api_key="correct-secret"))
    action_id = uuid4()
    blocked = client.get(f"/v1/distribution-actions/{action_id}")
    allowed_to_lookup = client.get(
        f"/v1/distribution-actions/{action_id}",
        headers={OPERATOR_KEY_HEADER: "correct-secret"},
    )
    assert blocked.status_code == 401
    assert allowed_to_lookup.status_code == 404
