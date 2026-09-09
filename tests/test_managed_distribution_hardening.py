import pytest
from fastapi.testclient import TestClient

from app.customer_account import (
    CUSTOMER_ACCOUNT_SESSION_COOKIE,
    customer_account_service,
)
from app.customer_funnel import customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.distribution_control_plane_service import distribution_control_plane_service
from app.main import app
from app.managed_distribution import managed_distribution_service
from app.product_intake import product_intake_service


@pytest.fixture(autouse=True)
def reset_managed_hardening_state():
    previous_ready = managed_distribution_service._settings.managed_distribution_public_ready
    managed_distribution_service._settings.managed_distribution_public_ready = False
    customer_account_service.reset()
    customer_funnel_service.reset()
    product_intake_service.reset()
    distribution_control_plane_service.reset()
    managed_distribution_service.reset()
    try:
        yield
    finally:
        managed_distribution_service._settings.managed_distribution_public_ready = previous_ready


def _managed_inventory(client: TestClient) -> tuple[dict, dict]:
    identity_response = client.post(
        "/v1/distribution-identities",
        json={
            "platform": "INSTAGRAM",
            "theme": "Freelancer operations",
            "language": "English",
            "public_positioning": "Partizan freelancer operations publisher",
            "allowed_opportunity_kinds": ["CREATOR_ACCOUNT"],
            "allowed_actions": ["COMMENT"],
        },
    )
    assert identity_response.status_code == 201
    identity = identity_response.json()
    publisher_response = client.post(
        "/v1/managed-distribution/publishers",
        json={
            "distribution_identity_id": identity["id"],
            "ownership": "PARTNER_MANAGED",
            "internal_label": "Verified Partner Publisher",
            "topic_verticals": ["freelancers", "operations"],
            "languages": ["English"],
            "allowed_surfaces": ["CREATOR_ACCOUNT"],
            "allowed_actions": ["COMMENT"],
            "daily_action_capacity": 2,
            "prior_outcome_score": 80,
            "management_authorization_confirmed": True,
            "partner_reference": "partner-contract-2026-09",
        },
    )
    assert publisher_response.status_code == 201
    return identity, publisher_response.json()


def _registered_customer(client: TestClient):
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with a monthly subscription.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
    )
    response = client.post(
        "/customer/account/register",
        json={
            "email": "managed-distribution@example.com",
            "password": "correct-horse-42",
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )
    assert response.status_code == 200
    return preview


def _instagram(payload: list[dict]) -> dict:
    return next(item for item in payload if item["platform"] == "INSTAGRAM")


def test_managed_mode_requires_public_readiness_and_live_eligible_inventory() -> None:
    client = TestClient(app)
    _, publisher = _managed_inventory(client)
    preview = _registered_customer(client)

    before = client.get(f"/customer/workspace/{preview.project_id}/channels")
    assert before.status_code == 200
    before_modes = {item["mode"]: item for item in _instagram(before.json())["publisher_modes"]}
    assert before_modes["PARTIZAN_MANAGED"]["available"] is False
    assert "not enabled" in before_modes["PARTIZAN_MANAGED"]["blocker"]

    managed_distribution_service._settings.managed_distribution_public_ready = True
    enabled = client.get(f"/customer/workspace/{preview.project_id}/channels")
    assert enabled.status_code == 200
    enabled_modes = {item["mode"]: item for item in _instagram(enabled.json())["publisher_modes"]}
    assert enabled_modes["PARTIZAN_MANAGED"]["available"] is True

    selected = client.put(
        f"/customer/workspace/{preview.project_id}/channels",
        json={
            "channels": [
                {"platform": "INSTAGRAM", "publisher_mode": "PARTIZAN_MANAGED"}
            ]
        },
    )
    assert selected.status_code == 200
    instagram = _instagram(selected.json())
    assert instagram["publisher_mode"] == "PARTIZAN_MANAGED"
    capabilities = {item["capability"]: item for item in instagram["capabilities"]}
    assert capabilities["PUBLISH"]["ready"] is True
    assert capabilities["MEASURE"]["ready"] is True

    paused = client.patch(
        f"/v1/managed-distribution/publishers/{publisher['id']}/health",
        json={"health": "PAUSED", "reason": "partner review"},
    )
    assert paused.status_code == 200
    after = client.get(f"/customer/workspace/{preview.project_id}/channels")
    assert after.status_code == 200
    instagram = _instagram(after.json())
    assert instagram["publisher_mode"] == "MANUAL"
    after_modes = {item["mode"]: item for item in instagram["publisher_modes"]}
    assert after_modes["PARTIZAN_MANAGED"]["available"] is False
    assert "no eligible managed publisher" in after_modes["PARTIZAN_MANAGED"]["blocker"]


def test_customer_assignment_route_hides_internal_publisher_mechanics() -> None:
    client = TestClient(app)
    _managed_inventory(client)
    preview = _registered_customer(client)
    managed_distribution_service._settings.managed_distribution_public_ready = True
    session_token = client.cookies.get(CUSTOMER_ACCOUNT_SESSION_COOKIE)
    _, customer_token = customer_account_service.project_access(
        session_token=session_token,
        project_id=preview.project_id,
    )
    project = customer_funnel_service.get_project_payload(
        preview.project_id,
        customer_token,
    )
    product_id = project["product_id"]

    reserved = client.post(
        f"/v1/products/{product_id}/managed-distribution/assignments",
        json={
            "platform": "INSTAGRAM",
            "action_type": "COMMENT",
            "opportunity_kind": "CREATOR_ACCOUNT",
            "vertical": "freelancer operations",
            "language": "English",
            "conflict_group": "bookkeeping-tools",
        },
    )
    assert reserved.status_code == 201
    assert "managed_publisher_id" in reserved.json()
    assert "distribution_identity_id" in reserved.json()

    customer_view = client.get(
        f"/customer/workspace/{preview.project_id}/managed-distribution/assignments"
    )
    assert customer_view.status_code == 200
    rows = customer_view.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["service_label"] == "Partizan Managed Distribution"
    assert row["status"] == "RESERVED"
    assert row["ownership"] == "PARTNER_MANAGED"
    assert "managed_publisher_id" not in row
    assert "distribution_identity_id" not in row
    assert "partner_reference" not in row
    assert "internal_label" not in row


def test_customer_cannot_read_another_projects_managed_assignments() -> None:
    client_a = TestClient(app)
    preview_a = _registered_customer(client_a)

    client_b = TestClient(app)
    preview_b = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI invoicing assistant for independent consultants.",
            website_url="https://example.org",
            market="United States",
            goal="Get paying customers",
            budget_usd=500,
        )
    )
    registered = client_b.post(
        "/customer/account/register",
        json={
            "email": "managed-distribution-other@example.com",
            "password": "correct-horse-42",
            "project_id": str(preview_b.project_id),
            "customer_token": preview_b.customer_token,
        },
    )
    assert registered.status_code == 200

    foreign = client_b.get(
        f"/customer/workspace/{preview_a.project_id}/managed-distribution/assignments"
    )
    assert foreign.status_code == 403
