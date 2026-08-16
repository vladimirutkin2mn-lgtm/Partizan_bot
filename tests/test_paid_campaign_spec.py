from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.execution_adapters import distribution_execution_adapter_service
from app.icp_service import icp_service
from app.main import app
from app.paid_campaign import (
    PAID_CAMPAIGN_SPEC_NAMESPACE,
    PaidCampaignSpec,
    PaidCampaignSpecService,
    paid_campaign_spec_service,
)
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
    distribution_execution_adapter_service.reset()
    paid_campaign_spec_service.reset()


def _product() -> str:
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
                "Max CAC: 5\n"
                "Goal: Acquire 100 paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    return product_id


def _paid_action(product_id: str, tactic_id: str, *, auto: bool = False) -> dict:
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert plays.status_code == 200
    play = next(item for item in plays.json()["plays"] if item["tactic_id"] == tactic_id)
    endpoint = (
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/auto-prepare"
        if auto
        else f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare"
    )
    response = client.post(endpoint, json={"destination_url": "https://example.com/oracle"})
    assert response.status_code == 200
    return response.json()["action"]


@pytest.mark.parametrize(
    ("tactic_id", "platform"),
    [
        ("telegram_ads", "TELEGRAM"),
        ("instagram_ads", "INSTAGRAM"),
        ("reddit_ads", "REDDIT"),
        ("tiktok_ads", "TIKTOK"),
    ],
)
def test_every_mvp_paid_tactic_produces_provider_ready_spec(
    tactic_id: str,
    platform: str,
) -> None:
    product_id = _product()
    action = _paid_action(product_id, tactic_id)

    response = client.get(
        f"/v1/distribution-actions/{action['id']}/paid-campaign-spec"
    )

    assert response.status_code == 200
    spec = response.json()
    assert spec["action_id"] == action["id"]
    assert spec["platform"] == platform
    assert spec["tactic_id"] == tactic_id
    assert spec["launch_mode"] == "CREATE_PAUSED"
    assert spec["objective"] == "ACQUISITION"
    assert spec["optimization_event"] == "PAID"
    assert spec["budget_cap"] <= 200
    assert spec["audience"]["icp"]["title"]
    assert spec["audience"]["opportunity"]["canonical_key"]
    assert spec["audience"]["platform_signals"] is not None
    assert spec["creative_brief"]["product_name"] == "Oracle"
    assert "ptz_experiment" in spec["destination_url"]


def test_paid_budget_never_exceeds_product_or_play_cap() -> None:
    product_id = _product()
    action = _paid_action(product_id, "instagram_ads")

    spec = paid_campaign_spec_service.get(UUID(action["id"]))

    assert spec is not None
    assert spec.budget_cap == 200
    assert spec.target_cac == 5
    assert "3x target CAC" in spec.kill_criteria
    assert "15.00" in spec.kill_criteria


def test_auto_prepare_also_creates_paid_spec() -> None:
    product_id = _product()
    action = _paid_action(product_id, "tiktok_ads", auto=True)

    spec = paid_campaign_spec_service.get(UUID(action["id"]))

    assert spec is not None
    assert spec.launch_mode.value == "CREATE_PAUSED"
    assert spec.creative_brief["message_hook"]


def test_paid_spec_survives_service_recreation() -> None:
    product_id = _product()
    action = _paid_action(product_id, "reddit_ads")
    action_id = UUID(action["id"])
    original = paid_campaign_spec_service.get(action_id)
    assert original is not None

    recreated = PaidCampaignSpecService(store=get_runtime_store())
    restored = recreated.get(action_id)

    assert restored == original


def test_malformed_persisted_paid_spec_is_rejected_not_free_form_parsed() -> None:
    action_id = UUID("11111111-1111-1111-1111-111111111111")
    get_runtime_store().put(
        PAID_CAMPAIGN_SPEC_NAMESPACE,
        str(action_id),
        {
            "action_id": str(action_id),
            "platform": "INSTAGRAM",
            "launch_mode": "CREATE_PAUSED",
            "budget_cap": 100,
        },
    )
    recreated = PaidCampaignSpecService(store=get_runtime_store())

    with pytest.raises(ValidationError):
        recreated.get(action_id)


def test_paid_campaign_spec_model_rejects_activation_mode() -> None:
    with pytest.raises(ValidationError):
        PaidCampaignSpec.model_validate(
            {
                "action_id": "11111111-1111-1111-1111-111111111111",
                "experiment_id": "22222222-2222-2222-2222-222222222222",
                "product_id": "33333333-3333-3333-3333-333333333333",
                "play_id": "44444444-4444-4444-4444-444444444444",
                "opportunity_id": "55555555-5555-5555-5555-555555555555",
                "platform": "INSTAGRAM",
                "tactic_id": "instagram_ads",
                "launch_mode": "ACTIVATE",
                "objective": "ACQUISITION",
                "optimization_event": "PAID",
                "destination_url": "https://example.com",
                "budget_cap": 100,
                "audience": {"segment": "test"},
                "creative_brief": {"message": "test"},
                "success_metric": "paid users",
                "kill_criteria": "stop on poor economics",
                "created_at": "2026-08-11T00:00:00Z",
            }
        )
