from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.channel_service import channel_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.execution_adapters import (
    AdapterExecutionOutcome,
    ConfirmedMockExecutionAdapter,
    DistributionAdapterExecuteRequest,
    DistributionExecutionAdapterService,
    ExecutionAdapterRegistry,
    distribution_execution_adapter_service,
)
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.main import app
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    channel_service.reset()
    audience_intelligence_service.reset()
    growth_play_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    distribution_execution_adapter_service.reset()


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
                "Goal: Acquire 100 paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    return product_id


def _plays(product_id: str) -> list[dict]:
    response = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert response.status_code == 200
    return response.json()["plays"]


def _approved_paid_action(product_id: str, tactic_id: str = "instagram_ads") -> str:
    paid = next(play for play in _plays(product_id) if play["tactic_id"] == tactic_id)
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{paid['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert prepared.status_code == 200
    action_id = prepared.json()["action"]["id"]
    approved = client.post(f"/v1/distribution-actions/{action_id}/approve")
    assert approved.status_code == 200
    return action_id


def test_paid_adapter_is_unavailable_without_authenticated_provider() -> None:
    product_id = _product()
    action_id = _approved_paid_action(product_id)

    response = client.post(
        f"/v1/distribution-actions/{action_id}/execute",
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["receipt"]["outcome"] == "UNAVAILABLE"
    assert payload["receipt"]["adapter_name"] == "paid-campaign-unavailable"
    assert payload["plan"]["action"]["status"] == "APPROVED"
    assert payload["plan"]["experiment"]["status"] == "APPROVED"

    repeated = client.post(
        f"/v1/distribution-actions/{action_id}/execute",
        json={},
    )
    assert repeated.status_code == 200
    assert repeated.json()["receipt"]["created_at"] == payload["receipt"]["created_at"]


def test_third_party_community_execution_stays_assisted() -> None:
    product_id = _product()
    identity = client.post(
        "/v1/distribution-identities",
        json={
            "platform": "INSTAGRAM",
            "theme": "Relationship advice",
            "language": "English",
            "public_positioning": "Partizan-operated relationship tools account",
            "allowed_opportunity_kinds": ["CREATOR_ACCOUNT"],
            "allowed_actions": ["COMMENT"],
        },
    )
    assert identity.status_code == 201
    slot = client.post(
        f"/v1/products/{product_id}/campaign-slots",
        json={
            "distribution_identity_id": identity.json()["id"],
            "status": "ACTIVE",
            "attribution_route": "https://partizan.example/relationships",
        },
    )
    assert slot.status_code == 201

    comment = next(
        play
        for play in _plays(product_id)
        if play["tactic_id"] == "instagram_creator_comment"
        and play["status"] == "READY"
    )
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{comment['id']}/actions/prepare",
        json={
            "destination_url": "https://example.com/oracle",
            "target_url": "https://www.instagram.com/reel/example/",
            "context_text": "The Reel discusses uncertainty after a breakup.",
            "content_text": "Separating facts from assumptions can make the situation clearer.",
        },
    )
    assert prepared.status_code == 200
    action_id = prepared.json()["action"]["id"]
    assert client.post(f"/v1/distribution-actions/{action_id}/approve").status_code == 200

    response = client.post(
        f"/v1/distribution-actions/{action_id}/execute",
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["receipt"]["outcome"] == "ASSISTED"
    assert payload["receipt"]["requires_operator_confirmation"] is True
    assert payload["plan"]["action"]["status"] == "APPROVED"
    assert payload["plan"]["experiment"]["status"] == "APPROVED"


def test_adapter_cannot_run_before_explicit_approval() -> None:
    product_id = _product()
    paid = next(play for play in _plays(product_id) if play["tactic_id"] == "reddit_ads")
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{paid['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    action_id = prepared.json()["action"]["id"]

    response = client.post(
        f"/v1/distribution-actions/{action_id}/execute",
        json={},
    )

    assert response.status_code == 409
    assert "APPROVED" in response.json()["detail"]


def test_confirmed_provider_is_the_only_adapter_path_that_starts_experiment() -> None:
    product_id = _product()
    action_id = _approved_paid_action(product_id, tactic_id="tiktok_ads")
    service = DistributionExecutionAdapterService(
        registry=ExecutionAdapterRegistry([ConfirmedMockExecutionAdapter()]),
        store=get_runtime_store(),
    )

    result = service.execute(
        UUID(action_id),
        DistributionAdapterExecuteRequest(),
    )

    assert result.receipt.outcome == AdapterExecutionOutcome.EXECUTED
    assert result.receipt.provider == "mock"
    assert result.plan.action.status.value == "EXECUTED"
    assert result.plan.experiment.status.value == "RUNNING"
    assert result.plan.action.operational_metadata["external_reference"].startswith("mock:")

    repeated = service.execute(
        UUID(action_id),
        DistributionAdapterExecuteRequest(retry=True),
    )
    assert repeated.receipt.created_at == result.receipt.created_at
    assert repeated.plan.action.status.value == "EXECUTED"

    recreated = DistributionExecutionAdapterService(
        registry=ExecutionAdapterRegistry([ConfirmedMockExecutionAdapter()]),
        store=get_runtime_store(),
    )
    stored = recreated.get_receipt(UUID(action_id))
    assert stored is not None
    assert stored.external_reference == result.receipt.external_reference
