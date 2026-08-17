from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.customer_funnel import customer_funnel_service
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_customer_projects() -> None:
    customer_funnel_service.reset()


def _preview() -> dict:
    response = client.post(
        "/v1/customer-projects/preview",
        json={
            "brief": "AI bookkeeping assistant for freelancers with a monthly subscription.",
            "market": "United States",
            "goal": "Get paying customers",
            "budget_usd": 1000,
        },
    )
    assert response.status_code == 201
    return response.json()


def _paid_project() -> tuple[dict, str]:
    preview = _preview()
    project_id = UUID(preview["project_id"])
    session_id = "cs_test_paid_partizan"
    customer_funnel_service.mark_checkout_pending(
        project_id,
        preview["customer_token"],
        session_id,
    )
    assert customer_funnel_service.unlock_launch(
        project_id,
        stripe_checkout_session_id=session_id,
        stripe_customer_id="cus_test_partizan",
    )
    return preview, session_id


def test_paid_checkout_can_rotate_lost_browser_access(monkeypatch) -> None:
    preview, session_id = _paid_project()
    project_id = preview["project_id"]

    monkeypatch.setattr(
        "app.customer_routes.retrieve_launch_checkout",
        lambda **kwargs: {
            "id": session_id,
            "payment_status": "paid",
            "client_reference_id": project_id,
            "metadata": {
                "partizan_project_id": project_id,
                "partizan_entitlement": "launch_plan",
            },
        },
    )

    recovered = client.post(
        f"/v1/customer-projects/{project_id}/recover-access",
        json={"session_id": session_id},
    )

    assert recovered.status_code == 200
    new_token = recovered.json()["customer_token"]
    assert new_token != preview["customer_token"]

    old_access = client.get(
        f"/v1/customer-projects/{project_id}",
        headers={"X-Partizan-Customer-Token": preview["customer_token"]},
    )
    new_access = client.get(
        f"/v1/customer-projects/{project_id}",
        headers={"X-Partizan-Customer-Token": new_token},
    )
    assert old_access.status_code == 401
    assert new_access.status_code == 200
    assert new_access.json()["launch_unlocked"] is True


def test_recovery_rejects_paid_session_for_another_project(monkeypatch) -> None:
    preview, session_id = _paid_project()
    project_id = preview["project_id"]

    monkeypatch.setattr(
        "app.customer_routes.retrieve_launch_checkout",
        lambda **kwargs: {
            "id": session_id,
            "payment_status": "paid",
            "client_reference_id": str(UUID(int=1)),
            "metadata": {
                "partizan_project_id": str(UUID(int=1)),
                "partizan_entitlement": "launch_plan",
            },
        },
    )

    response = client.post(
        f"/v1/customer-projects/{project_id}/recover-access",
        json={"session_id": session_id},
    )

    assert response.status_code == 401
    still_valid = client.get(
        f"/v1/customer-projects/{project_id}",
        headers={"X-Partizan-Customer-Token": preview["customer_token"]},
    )
    assert still_valid.status_code == 200
