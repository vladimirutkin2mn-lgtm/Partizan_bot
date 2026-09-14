from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

import app.customer_execution_request_routes as route_module
from app.config import Settings, get_settings
from app.customer_execution_request_schemas import CustomerOperatorExecutionView
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_types import DistributionActionStatus
from app.main import app

REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")
ACTION_ID = UUID("66666666-6666-4666-8666-666666666666")


def _execution_view() -> CustomerOperatorExecutionView:
    return CustomerOperatorExecutionView(
        request_id=REQUEST_ID,
        distribution_action_id=ACTION_ID,
        action_status=DistributionActionStatus.APPROVED,
        experiment_status=DistributionExperimentStatus.APPROVED,
        receipt=None,
        retry_allowed=False,
    )


def test_customer_operator_execution_requires_auth_and_explicit_true(monkeypatch) -> None:
    view = _execution_view()
    execute_calls = []
    view_calls = []
    monkeypatch.setattr(
        route_module,
        "customer_operator_execution_service",
        SimpleNamespace(
            view=lambda request_id: view_calls.append(request_id) or view,
            execute=lambda request_id: execute_calls.append(request_id) or view,
        ),
    )
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_env="production",
        operator_api_key="operator-secret",
    )
    client = TestClient(app)
    path = f"/v1/customer-execution-requests/{REQUEST_ID}/execute-action"
    state_path = f"/v1/customer-execution-requests/{REQUEST_ID}/execution"
    headers = {"X-Partizan-Operator-Key": "operator-secret"}
    try:
        missing_auth = client.post(path, json={"confirm_execution": True})
        false_confirmation = client.post(
            path,
            headers=headers,
            json={"confirm_execution": False},
        )
        injected_action = client.post(
            path,
            headers=headers,
            json={
                "confirm_execution": True,
                "distribution_action_id": str(ACTION_ID),
            },
        )
        state = client.get(state_path, headers=headers)
        allowed = client.post(
            path,
            headers=headers,
            json={"confirm_execution": True},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert missing_auth.status_code == 401
    assert false_confirmation.status_code == 422
    assert injected_action.status_code == 422
    assert state.status_code == 200
    assert state.json()["retry_allowed"] is False
    assert allowed.status_code == 200
    assert allowed.json()["request_id"] == str(REQUEST_ID)
    assert allowed.json()["distribution_action_id"] == str(ACTION_ID)
    assert allowed.json()["action_status"] == "APPROVED"
    assert allowed.json()["retry_allowed"] is False
    assert view_calls == [REQUEST_ID]
    assert execute_calls == [REQUEST_ID]


def test_customer_operator_execution_errors_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        route_module,
        "customer_operator_execution_service",
        SimpleNamespace(
            view=lambda _request_id: (_ for _ in ()).throw(ValueError("fingerprint mismatch")),
            execute=lambda _request_id: (_ for _ in ()).throw(ValueError("fingerprint mismatch")),
        ),
    )
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_env="production",
        operator_api_key="operator-secret",
    )
    client = TestClient(app)
    headers = {"X-Partizan-Operator-Key": "operator-secret"}
    try:
        state = client.get(
            f"/v1/customer-execution-requests/{REQUEST_ID}/execution",
            headers=headers,
        )
        execute = client.post(
            f"/v1/customer-execution-requests/{REQUEST_ID}/execute-action",
            headers=headers,
            json={"confirm_execution": True},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert state.status_code == 409
    assert execute.status_code == 409
    assert state.json()["detail"] == "fingerprint mismatch"
    assert execute.json()["detail"] == "fingerprint mismatch"
