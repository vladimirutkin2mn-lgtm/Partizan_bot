from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.customer_account import customer_account_service
from app.customer_funnel import customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.icp_service import icp_service
from app.main import app
from app.managed_distribution import managed_distribution_service
from app.product_intake import product_intake_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state():
    previous_ready = managed_distribution_service._settings.managed_distribution_public_ready
    managed_distribution_service._settings.managed_distribution_public_ready = False
    product_intake_service.reset()
    icp_service.reset()
    audience_intelligence_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    managed_distribution_service.reset()
    customer_account_service.reset()
    customer_funnel_service.reset()
    try:
        yield
    finally:
        managed_distribution_service._settings.managed_distribution_public_ready = previous_ready


def _product(name: str = "Oracle") -> str:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                f"Product: {name}\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized readings available on demand.\n"
                "Market: US\n"
                "Language: English\n"
                "Budget: 200\n"
                "Goal: Acquire 100 paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    return product_id


def _identity(theme: str = "Relationship advice") -> dict:
    response = client.post(
        "/v1/distribution-identities",
        json={
            "platform": "INSTAGRAM",
            "theme": theme,
            "language": "English",
            "public_positioning": f"Partizan {theme} Publisher",
            "allowed_opportunity_kinds": ["CREATOR_ACCOUNT"],
            "allowed_actions": ["COMMENT"],
        },
    )
    assert response.status_code == 201
    return response.json()


def _register(
    identity_id: str,
    *,
    label: str = "Relationships Publisher A",
    ownership: str = "PARTIZAN_MANAGED",
    partner_reference: str | None = None,
    capacity: int = 3,
    outcome_score: float = 70,
    last_activity_at: str | None = None,
) -> dict:
    response = client.post(
        "/v1/managed-distribution/publishers",
        json={
            "distribution_identity_id": identity_id,
            "ownership": ownership,
            "internal_label": label,
            "topic_verticals": ["relationships", "relationship advice"],
            "languages": ["English"],
            "allowed_surfaces": ["CREATOR_ACCOUNT"],
            "allowed_actions": ["COMMENT"],
            "daily_action_capacity": capacity,
            "prior_outcome_score": outcome_score,
            "last_activity_at": last_activity_at,
            "management_authorization_confirmed": True,
            "partner_reference": partner_reference,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _selection_payload() -> dict:
    return {
        "platform": "INSTAGRAM",
        "action_type": "COMMENT",
        "opportunity_kind": "CREATOR_ACCOUNT",
        "vertical": "relationship advice",
        "language": "English",
        "conflict_group": "relationship-apps",
    }


def _prepare_approved_comment(product_id: str, identity_id: str) -> str:
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert plays.status_code == 200
    comment = next(
        play
        for play in plays.json()["plays"]
        if play["tactic_id"] == "instagram_creator_comment"
        and play["selected_identity_id"] == identity_id
        and play["status"] == "READY"
    )
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{comment['id']}/actions/prepare",
        json={
            "destination_url": "https://example.com/oracle",
            "target_url": "https://www.instagram.com/p/example/",
            "context_text": "Creator discusses uncertainty after a breakup.",
            "content_text": "Separate the facts from the assumptions before deciding what the signal means.",
        },
    )
    assert prepared.status_code == 200, prepared.text
    action_id = prepared.json()["action"]["id"]
    approved = client.post(f"/v1/distribution-actions/{action_id}/approve")
    assert approved.status_code == 200, approved.text
    return action_id


def test_managed_inventory_requires_explicit_authorization_and_partner_reference() -> None:
    identity = _identity()
    missing_authorization = client.post(
        "/v1/managed-distribution/publishers",
        json={
            "distribution_identity_id": identity["id"],
            "ownership": "PARTIZAN_MANAGED",
            "internal_label": "Publisher A",
            "topic_verticals": ["relationships"],
            "languages": ["English"],
            "allowed_surfaces": ["CREATOR_ACCOUNT"],
            "allowed_actions": ["COMMENT"],
            "management_authorization_confirmed": False,
        },
    )
    assert missing_authorization.status_code == 422

    partner = client.post(
        "/v1/managed-distribution/publishers",
        json={
            "distribution_identity_id": identity["id"],
            "ownership": "PARTNER_MANAGED",
            "internal_label": "Partner Publisher",
            "topic_verticals": ["relationships"],
            "languages": ["English"],
            "allowed_surfaces": ["CREATOR_ACCOUNT"],
            "allowed_actions": ["COMMENT"],
            "management_authorization_confirmed": True,
        },
    )
    assert partner.status_code == 422


def test_managed_readiness_is_fail_closed_even_with_registered_inventory() -> None:
    identity = _identity()
    _register(identity["id"])

    reserve = client.post(
        f"/v1/products/{_product()}/managed-distribution/assignments",
        json=_selection_payload(),
    )

    assert reserve.status_code == 409
    assert "not enabled" in reserve.json()["detail"]


def test_selection_uses_fit_activity_outcomes_health_and_capacity() -> None:
    first = _identity("Relationship advice")
    second = _identity("General lifestyle")
    recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    first_publisher = _register(
        first["id"],
        label="Strong fit",
        outcome_score=90,
        last_activity_at=recent,
    )
    second_publisher = _register(
        second["id"],
        label="Weak fit",
        outcome_score=20,
    )

    preview = client.post(
        "/v1/managed-distribution/selection/preview",
        json=_selection_payload(),
    )
    assert preview.status_code == 200
    rows = preview.json()
    assert rows[0]["managed_publisher_id"] == first_publisher["id"]
    assert rows[0]["score"] > rows[1]["score"]

    paused = client.patch(
        f"/v1/managed-distribution/publishers/{first_publisher['id']}/health",
        json={"health": "PAUSED", "reason": "manual review"},
    )
    assert paused.status_code == 200
    preview = client.post(
        "/v1/managed-distribution/selection/preview",
        json=_selection_payload(),
    )
    assert preview.status_code == 200
    assert [row["managed_publisher_id"] for row in preview.json()] == [
        second_publisher["id"]
    ]


def test_active_assignment_prevents_same_publisher_from_serving_conflicting_client() -> None:
    managed_distribution_service._settings.managed_distribution_public_ready = True
    identity = _identity()
    _register(identity["id"])
    first_product = _product("Oracle A")
    second_product = _product("Oracle B")

    first = client.post(
        f"/v1/products/{first_product}/managed-distribution/assignments",
        json=_selection_payload(),
    )
    assert first.status_code == 201

    second = client.post(
        f"/v1/products/{second_product}/managed-distribution/assignments",
        json=_selection_payload(),
    )
    assert second.status_code == 409
    assert "No eligible managed publisher" in second.json()["detail"]

    released = client.delete(
        f"/v1/managed-distribution/assignments/{first.json()['id']}"
    )
    assert released.status_code == 200
    retry = client.post(
        f"/v1/products/{second_product}/managed-distribution/assignments",
        json=_selection_payload(),
    )
    assert retry.status_code == 201


def test_fulfillment_records_separate_costs_internal_audit_and_capacity() -> None:
    managed_distribution_service._settings.managed_distribution_public_ready = True
    identity = _identity()
    publisher = _register(identity["id"], capacity=1)
    product_id = _product()

    reserved = client.post(
        f"/v1/products/{product_id}/managed-distribution/assignments",
        json=_selection_payload(),
    )
    assert reserved.status_code == 201, reserved.text
    assignment = reserved.json()
    action_id = _prepare_approved_comment(product_id, identity["id"])

    fulfilled = client.post(
        f"/v1/managed-distribution/assignments/{assignment['id']}/fulfill",
        json={
            "action_id": action_id,
            "external_reference": "managed-result-123",
            "executed_url": "https://www.instagram.com/p/result123/",
            "distribution_spend_usd": 25,
            "operational_cost_usd": 7,
            "management_fee_usd": 5,
        },
    )
    assert fulfilled.status_code == 200, fulfilled.text
    result = fulfilled.json()
    assert result["status"] == "FULFILLED"
    assert result["managed_publisher_id"] == publisher["id"]
    assert result["distribution_identity_id"] == identity["id"]
    assert result["cost"] == {
        "distribution_spend_usd": 25.0,
        "operational_cost_usd": 7.0,
        "management_fee_usd": 5.0,
    }

    action = distribution_execution_service.get_action(action_id)
    assert action.status.value == "EXECUTED"
    managed_observation = action.operational_metadata["external_observations"][
        "partizan_managed"
    ]
    assert managed_observation["assignment_id"] == assignment["id"]
    assert "managed_publisher_id" not in managed_observation
    assert managed_distribution_service.capacity_remaining_24h(
        publisher["id"]
    ) == 0

    customer_safe = managed_distribution_service.list_customer_assignments(
        product_intake_service.get_product(product_id).id
    )[0].model_dump(mode="json")
    assert customer_safe["service_label"] == "Partizan Managed Distribution"
    assert "managed_publisher_id" not in customer_safe
    assert "distribution_identity_id" not in customer_safe
    assert "partner_reference" not in customer_safe


def test_managed_service_has_no_account_farm_or_engagement_abuse_surface() -> None:
    forbidden = {
        "create_account",
        "clone_account",
        "vote",
        "upvote",
        "downvote",
        "send_dm",
        "mass_message",
        "join_community",
        "evade_ban",
    }
    assert forbidden.isdisjoint(set(dir(managed_distribution_service)))
