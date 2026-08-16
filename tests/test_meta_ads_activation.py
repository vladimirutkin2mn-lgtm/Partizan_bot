from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.execution_adapters import (
    EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
    AdapterExecutionOutcome,
    ExecutionAdapterReceipt,
    distribution_execution_adapter_service,
)
from app.icp_service import icp_service
from app.main import app
from app.meta_marketing_api import MetaMarketingApiError
from app.paid_activation import (
    PaidActivationAuthorizationRequest,
    PaidActivationRequest,
    PaidActivationService,
    paid_activation_service,
)
from app.paid_campaign import paid_campaign_spec_service
from app.paid_provider_connections import (
    PaidProviderConnectionCreateRequest,
    paid_provider_connection_service,
)
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store

client = TestClient(app)


class FakeSecretResolver:
    def resolve(self, name: str) -> str | None:
        assert name == "META_TEST_TOKEN"
        return "activation-secret-token"


class FakeActivationMetaClient:
    def __init__(self, fail_object_id: str | None = None) -> None:
        self.fail_object_id = fail_object_id
        self.calls: list[tuple[str, str]] = []

    def set_status(self, **kwargs) -> None:
        object_id = str(kwargs["object_id"])
        status = str(kwargs["status"])
        assert kwargs["access_token"] == "activation-secret-token"
        self.calls.append((object_id, status))
        if object_id == self.fail_object_id:
            raise MetaMarketingApiError(f"activation failed for {object_id}")


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
    paid_provider_connection_service.reset()
    paid_activation_service.reset()


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


def _approved_paid_action(product_id: str) -> str:
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert plays.status_code == 200
    play = next(
        item for item in plays.json()["plays"] if item["tactic_id"] == "instagram_ads"
    )
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert prepared.status_code == 200
    action_id = prepared.json()["action"]["id"]
    assert client.post(f"/v1/distribution-actions/{action_id}/approve").status_code == 200
    return action_id


def _connect_meta(product_id: str) -> None:
    paid_provider_connection_service.upsert_meta(
        UUID(product_id),
        PaidProviderConnectionCreateRequest(
            ad_account_id="123456",
            page_id="page_123",
            instagram_actor_id="ig_123",
            access_token_env="META_TEST_TOKEN",
            api_version="v99.0",
            country_codes=["US"],
            default_image_url="https://cdn.example.com/oracle.jpg",
            test_days=5,
        ),
    )


def _stage(action_id: str) -> ExecutionAdapterReceipt:
    receipt = ExecutionAdapterReceipt(
        action_id=UUID(action_id),
        adapter_name="meta-ads-create-paused",
        provider="meta-marketing-api",
        outcome=AdapterExecutionOutcome.STAGED,
        message="Meta objects staged in paused state.",
        external_reference="meta:ad:ad_123",
        metadata={
            "provider_ids": {
                "campaign_id": "cmp_123",
                "ad_set_id": "set_123",
                "creative_id": "creative_123",
                "ad_id": "ad_123",
            },
            "all_spend_objects_status": "PAUSED",
            "spend_started": False,
            "launch_mode": "CREATE_PAUSED",
        },
        created_at=datetime.now(UTC),
    )
    get_runtime_store().put(
        EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
        action_id,
        receipt.model_dump(mode="json"),
    )
    return receipt


def _activation_service(fake: FakeActivationMetaClient, *, ttl: int = 15) -> PaidActivationService:
    return PaidActivationService(
        store=get_runtime_store(),
        meta_client=fake,
        secret_resolver=FakeSecretResolver(),
        connection_service=paid_provider_connection_service,
        spec_service=paid_campaign_spec_service,
        authorization_ttl_minutes=ttl,
    )


def test_authorization_requires_staged_receipt_confirmation_and_exact_budget() -> None:
    product_id = _product()
    action_id = _approved_paid_action(product_id)
    spec = paid_campaign_spec_service.get(UUID(action_id))
    assert spec is not None

    before_stage = client.post(
        f"/v1/distribution-actions/{action_id}/paid-campaign/activation-authorizations",
        json={"approved_budget_cap": spec.budget_cap, "confirm_spend": True},
    )
    assert before_stage.status_code == 409

    _stage(action_id)
    not_confirmed = client.post(
        f"/v1/distribution-actions/{action_id}/paid-campaign/activation-authorizations",
        json={"approved_budget_cap": spec.budget_cap, "confirm_spend": False},
    )
    assert not_confirmed.status_code == 409
    assert "confirm_spend" in not_confirmed.json()["detail"]

    wrong_budget = client.post(
        f"/v1/distribution-actions/{action_id}/paid-campaign/activation-authorizations",
        json={"approved_budget_cap": spec.budget_cap - 1, "confirm_spend": True},
    )
    assert wrong_budget.status_code == 409
    assert "exactly match" in wrong_budget.json()["detail"]

    allowed = client.post(
        f"/v1/distribution-actions/{action_id}/paid-campaign/activation-authorizations",
        json={"approved_budget_cap": spec.budget_cap, "confirm_spend": True},
    )
    assert allowed.status_code == 201
    authorization = allowed.json()
    assert authorization["approved_budget_cap"] == spec.budget_cap
    assert authorization["attempted_at"] is None
    assert authorization["consumed_at"] is None
    assert "activation-secret-token" not in str(authorization)


def test_successful_activation_is_campaign_last_and_starts_experiment() -> None:
    product_id = _product()
    action_id = _approved_paid_action(product_id)
    _connect_meta(product_id)
    _stage(action_id)
    spec = paid_campaign_spec_service.get(UUID(action_id))
    assert spec is not None
    fake = FakeActivationMetaClient()
    service = _activation_service(fake)
    authorization = service.authorize(
        UUID(action_id),
        PaidActivationAuthorizationRequest(
            approved_budget_cap=spec.budget_cap,
            confirm_spend=True,
        ),
    )

    result = service.activate(
        UUID(action_id),
        PaidActivationRequest(authorization_id=authorization.id),
    )

    assert fake.calls == [
        ("ad_123", "ACTIVE"),
        ("set_123", "ACTIVE"),
        ("cmp_123", "ACTIVE"),
    ]
    assert result.receipt.outcome == AdapterExecutionOutcome.EXECUTED
    assert result.receipt.metadata["activation_steps_completed"] == [
        "ad",
        "ad_set",
        "campaign",
    ]
    assert result.receipt.metadata["spend_started"] is True
    assert result.receipt.metadata["spend_state"] == "ACTIVE"
    assert result.plan.action.status.value == "EXECUTED"
    assert result.plan.experiment.status.value == "RUNNING"
    stored_authorization = service.get_authorization(authorization.id)
    assert stored_authorization.attempted_at is not None
    assert stored_authorization.consumed_at is not None
    assert "activation-secret-token" not in result.receipt.model_dump_json()

    with pytest.raises(ValueError, match="APPROVED"):
        service.activate(
            UUID(action_id),
            PaidActivationRequest(authorization_id=authorization.id),
        )


def test_activation_failure_before_campaign_keeps_experiment_stopped_and_requires_reconcile() -> None:
    product_id = _product()
    action_id = _approved_paid_action(product_id)
    _connect_meta(product_id)
    _stage(action_id)
    spec = paid_campaign_spec_service.get(UUID(action_id))
    assert spec is not None
    fake = FakeActivationMetaClient(fail_object_id="set_123")
    service = _activation_service(fake)
    authorization = service.authorize(
        UUID(action_id),
        PaidActivationAuthorizationRequest(
            approved_budget_cap=spec.budget_cap,
            confirm_spend=True,
        ),
    )

    result = service.activate(
        UUID(action_id),
        PaidActivationRequest(authorization_id=authorization.id),
    )

    assert fake.calls == [("ad_123", "ACTIVE"), ("set_123", "ACTIVE")]
    assert result.receipt.outcome == AdapterExecutionOutcome.STAGED
    assert result.receipt.requires_operator_confirmation is True
    assert result.receipt.metadata["activation_steps_completed"] == ["ad"]
    assert result.receipt.metadata["spend_state"] == "NOT_STARTED"
    assert result.receipt.metadata["requires_reconciliation"] is True
    assert result.plan.action.status.value == "APPROVED"
    assert result.plan.experiment.status.value == "APPROVED"
    stored_authorization = service.get_authorization(authorization.id)
    assert stored_authorization.attempted_at is not None
    assert stored_authorization.consumed_at is None

    with pytest.raises(ValueError, match="already been attempted"):
        service.activate(
            UUID(action_id),
            PaidActivationRequest(authorization_id=authorization.id),
        )
    with pytest.raises(ValueError, match="Reconcile"):
        service.authorize(
            UUID(action_id),
            PaidActivationAuthorizationRequest(
                approved_budget_cap=spec.budget_cap,
                confirm_spend=True,
            ),
        )


def test_expired_authorization_cannot_activate() -> None:
    product_id = _product()
    action_id = _approved_paid_action(product_id)
    _connect_meta(product_id)
    _stage(action_id)
    spec = paid_campaign_spec_service.get(UUID(action_id))
    assert spec is not None
    fake = FakeActivationMetaClient()
    service = _activation_service(fake, ttl=0)
    authorization = service.authorize(
        UUID(action_id),
        PaidActivationAuthorizationRequest(
            approved_budget_cap=spec.budget_cap,
            confirm_spend=True,
        ),
    )

    with pytest.raises(ValueError, match="expired"):
        service.activate(
            UUID(action_id),
            PaidActivationRequest(authorization_id=authorization.id),
        )
    assert fake.calls == []
