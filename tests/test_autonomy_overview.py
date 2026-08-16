from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.autonomous_growth import (
    AUTONOMOUS_GROWTH_DECISION_NAMESPACE,
    AUTONOMOUS_GROWTH_RUN_NAMESPACE,
)
from app.autonomous_growth_control import AUTONOMOUS_GROWTH_CONTROL_AUDIT_NAMESPACE
from app.autonomous_paid import AUTONOMOUS_PAID_ACTIVATION_AUDIT_NAMESPACE
from app.autonomy_service import growth_mandate_service
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_schemas import DistributionActionExecutionRequest
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_service import distribution_play_service
from app.icp_service import icp_service
from app.main import app
from app.paid_campaign import paid_campaign_spec_service
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    audience_intelligence_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    distribution_analytics_service.reset()
    distribution_growth_manager_service.reset()
    paid_campaign_spec_service.reset()
    growth_mandate_service.reset()
    store = get_runtime_store()
    if store.ephemeral:
        for namespace in (
            AUTONOMOUS_GROWTH_RUN_NAMESPACE,
            AUTONOMOUS_GROWTH_DECISION_NAMESPACE,
            AUTONOMOUS_PAID_ACTIVATION_AUDIT_NAMESPACE,
            AUTONOMOUS_GROWTH_CONTROL_AUDIT_NAMESPACE,
        ):
            store.clear_namespace(namespace)


def _create_product() -> str:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized readings available on demand.\n"
                "Market: US\n"
                "Language: English\n"
                "Budget: 200\n"
                "Max CAC: 12\n"
                "Goal: Acquire paid users"
            ),
            "reference_links": ["https://example.com/oracle"],
        },
    )
    assert response.status_code == 201
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    return product_id


def _add_tiktok_identity(product_id: str) -> None:
    identity = client.post(
        "/v1/distribution-identities",
        json={
            "platform": "TIKTOK",
            "theme": "Relationship advice",
            "language": "English",
            "public_positioning": "Partizan relationship reflection account",
            "allowed_opportunity_kinds": ["CONTENT_CLUSTER"],
            "allowed_actions": ["ORGANIC_VIDEO"],
        },
    )
    assert identity.status_code == 201
    slot = client.post(
        f"/v1/products/{product_id}/campaign-slots",
        json={
            "distribution_identity_id": identity.json()["id"],
            "status": "ACTIVE",
            "attribution_route": "https://example.com/oracle",
        },
    )
    assert slot.status_code == 201


def _generate_plays(product_id: str) -> list[dict]:
    response = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert response.status_code == 200
    return response.json()["plays"]


def _set_mandate(product_id: str) -> dict:
    response = client.put(
        f"/v1/products/{product_id}/growth-mandate",
        json={
            "total_budget_cap": 200,
            "target_max_cac": 12,
            "max_autonomous_spend_per_experiment": 50,
            "max_autonomous_spend_per_day": 100,
            "max_concurrent_running_experiments": 2,
            "allowed_platforms": ["TIKTOK", "INSTAGRAM"],
            "allowed_actions": ["ORGANIC_VIDEO", "PAID_CAMPAIGN"],
            "autonomous_prepare": True,
            "autonomous_approve": False,
            "autonomous_paid_activation": False,
            "approval_threshold": 50,
        },
    )
    assert response.status_code == 200
    return response.json()


def test_overview_without_mandate_is_safe_and_empty() -> None:
    product_id = _create_product()

    response = client.get(f"/v1/products/{product_id}/autonomy-overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["product_id"] == product_id
    assert payload["mandate"] is None
    assert payload["remaining_total_budget"] == 0
    assert payload["remaining_daily_budget"] == 0
    assert payload["running_experiments"] == []
    assert payload["waiting_approval"] == []
    assert payload["recent_decisions"] == []


def test_overview_separates_running_from_waiting_and_shows_paid_cap() -> None:
    product_id = _create_product()
    _add_tiktok_identity(product_id)
    plays = _generate_plays(product_id)
    mandate = _set_mandate(product_id)

    organic = next(
        play
        for play in plays
        if play["tactic_id"] == "tiktok_partizan_organic_video"
        and play["status"] == "READY"
    )
    organic_prepare = client.post(
        f"/v1/products/{product_id}/distribution-plays/{organic['id']}/actions/auto-prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert organic_prepare.status_code == 200
    organic_action_id = UUID(organic_prepare.json()["action"]["id"])
    assert client.post(f"/v1/distribution-actions/{organic_action_id}/approve").status_code == 200
    distribution_execution_service.mark_executed(
        organic_action_id,
        DistributionActionExecutionRequest(
            external_reference="overview-test",
            notes="Simulated confirmed organic execution",
        ),
    )

    paid = next(play for play in plays if play["tactic_id"] == "instagram_ads")
    paid_prepare = client.post(
        f"/v1/products/{product_id}/distribution-plays/{paid['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert paid_prepare.status_code == 200
    paid_action_id = UUID(paid_prepare.json()["action"]["id"])
    paid_spec = paid_campaign_spec_service.get(paid_action_id)
    assert paid_spec is not None

    response = client.get(f"/v1/products/{product_id}/autonomy-overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mandate"]["id"] == mandate["id"]
    assert payload["mandate"]["version"] == mandate["version"]
    assert len(payload["running_experiments"]) == 1
    assert payload["running_experiments"][0]["action_id"] == str(organic_action_id)
    assert payload["running_experiments"][0]["experiment_status"] == "RUNNING"
    assert len(payload["waiting_approval"]) == 1
    waiting = payload["waiting_approval"][0]
    assert waiting["action_id"] == str(paid_action_id)
    assert waiting["experiment_status"] == "DRAFT"
    assert waiting["action_type"] == "PAID_CAMPAIGN"
    assert waiting["budget_cap"] == paid_spec.budget_cap
    assert payload["budget_exposure"]["reserved_running_paid_budget"] == 0
    assert payload["remaining_total_budget"] == 200
    assert payload["remaining_daily_budget"] == 100
