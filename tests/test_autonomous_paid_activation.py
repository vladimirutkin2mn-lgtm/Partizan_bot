from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.autonomous_paid import (
    AUTONOMOUS_PAID_ACTIVATION_AUDIT_NAMESPACE,
    AutonomousPaidActivationCoordinator,
)
from app.autonomy_schemas import GrowthMandateStatus
from app.autonomy_service import growth_mandate_service
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_schemas import DistributionActionExecutionRequest
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.execution_adapters import (
    EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
    AdapterExecutionOutcome,
    DistributionAdapterExecutionView,
    ExecutionAdapterReceipt,
    distribution_execution_adapter_service,
)
from app.icp_service import icp_service
from app.main import app
from app.paid_campaign import paid_campaign_spec_service
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store

client = TestClient(app)


class FakeMetaActivationBoundary:
    def __init__(self, *, pause_after_authorize: UUID | None = None) -> None:
        self.authorize_calls: list[tuple[UUID, float, bool]] = []
        self.activate_calls: list[tuple[UUID, UUID]] = []
        self.pause_after_authorize = pause_after_authorize

    def authorize(self, action_id, payload):
        self.authorize_calls.append(
            (action_id, payload.approved_budget_cap, payload.confirm_spend)
        )
        authorization = SimpleNamespace(id=uuid4())
        if self.pause_after_authorize is not None:
            growth_mandate_service.set_status(
                self.pause_after_authorize,
                GrowthMandateStatus.PAUSED,
            )
        return authorization

    def activate(self, action_id, payload):
        self.activate_calls.append((action_id, payload.authorization_id))
        staged = distribution_execution_adapter_service.get_receipt(action_id)
        assert staged is not None
        executed = staged.model_copy(
            update={
                "outcome": AdapterExecutionOutcome.EXECUTED,
                "message": "Fake provider activation confirmed.",
                "metadata": {**staged.metadata, "spend_started": True, "spend_state": "ACTIVE"},
                "created_at": datetime.now(UTC),
            }
        )
        plan = distribution_execution_service.mark_executed(
            action_id,
            DistributionActionExecutionRequest(
                external_reference=executed.external_reference,
                notes="Fake delegated paid activation",
            ),
        )
        get_runtime_store().put(
            EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
            str(action_id),
            executed.model_dump(mode="json"),
        )
        return DistributionAdapterExecutionView(receipt=executed, plan=plan)


class NeverTikTokActivationBoundary:
    def authorize(self, action_id, payload):
        raise AssertionError("TikTok boundary should not be called in Meta tests")

    def activate(self, action_id, payload):
        raise AssertionError("TikTok boundary should not be called in Meta tests")


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    audience_intelligence_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    distribution_execution_adapter_service.reset()
    distribution_analytics_service.reset()
    paid_campaign_spec_service.reset()
    growth_mandate_service.reset()
    store = get_runtime_store()
    if store.ephemeral:
        store.clear_namespace(AUTONOMOUS_PAID_ACTIVATION_AUDIT_NAMESPACE)


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
                "Max CAC: 12\n"
                "Goal: Acquire paid users"
            )
        },
    )
    assert response.status_code == 201
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution-plays/generate").status_code == 200
    return product_id


def _paid_action(product_id: str) -> UUID:
    plays = client.get(f"/v1/products/{product_id}/distribution-plays")
    assert plays.status_code == 200
    play = next(item for item in plays.json()["plays"] if item["tactic_id"] == "instagram_ads")
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert prepared.status_code == 200
    action_id = UUID(prepared.json()["action"]["id"])
    assert client.post(f"/v1/distribution-actions/{action_id}/approve").status_code == 200
    _stage(action_id)
    return action_id


def _stage(action_id: UUID) -> None:
    receipt = ExecutionAdapterReceipt(
        action_id=action_id,
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
        str(action_id),
        receipt.model_dump(mode="json"),
    )


def _mandate(product_id: str, *, paid_activation: bool, daily_cap: float = 300) -> dict:
    response = client.put(
        f"/v1/products/{product_id}/growth-mandate",
        json={
            "total_budget_cap": 400,
            "target_max_cac": 12,
            "max_autonomous_spend_per_experiment": 200,
            "max_autonomous_spend_per_day": daily_cap,
            "max_concurrent_running_experiments": 3,
            "allowed_platforms": ["INSTAGRAM"],
            "allowed_actions": ["PAID_CAMPAIGN"],
            "autonomous_prepare": True,
            "autonomous_approve": True,
            "autonomous_paid_activation": paid_activation,
            "approval_threshold": 200,
        },
    )
    assert response.status_code == 200
    return response.json()


def _coordinator(fake: FakeMetaActivationBoundary) -> AutonomousPaidActivationCoordinator:
    return AutonomousPaidActivationCoordinator(
        store=get_runtime_store(),
        mandate_service=growth_mandate_service,
        spec_service=paid_campaign_spec_service,
        meta_activation=fake,
        tiktok_activation=NeverTikTokActivationBoundary(),
    )


def test_paid_activation_without_delegation_stays_staged_and_requests_approval() -> None:
    product_id = _product()
    action_id = _paid_action(product_id)
    mandate = _mandate(product_id, paid_activation=False)
    fake = FakeMetaActivationBoundary()

    result = _coordinator(fake).activate_staged(
        mandate=growth_mandate_service.get(UUID(product_id)),
        action_id=action_id,
    )

    assert result.outcome == "REQUIRE_APPROVAL"
    assert result.authorization_id is None
    assert fake.authorize_calls == []
    assert fake.activate_calls == []
    plan = distribution_execution_service.get_plan(action_id)
    assert plan.action.status == "APPROVED"
    assert plan.experiment.status == "APPROVED"
    assert mandate["autonomous_paid_activation"] is False


def test_delegated_paid_activation_uses_exact_persisted_budget() -> None:
    product_id = _product()
    action_id = _paid_action(product_id)
    _mandate(product_id, paid_activation=True)
    spec = paid_campaign_spec_service.get(action_id)
    assert spec is not None
    assert spec.budget_cap == 200
    fake = FakeMetaActivationBoundary()

    result = _coordinator(fake).activate_staged(
        mandate=growth_mandate_service.get(UUID(product_id)),
        action_id=action_id,
    )

    assert result.outcome == "ACTIVATED"
    assert fake.authorize_calls == [(action_id, 200, True)]
    assert len(fake.activate_calls) == 1
    assert fake.activate_calls[0][0] == action_id
    assert fake.activate_calls[0][1] == result.authorization_id
    assert result.exact_budget_cap == 200
    plan = distribution_execution_service.get_plan(action_id)
    assert plan.action.status == "EXECUTED"
    assert plan.experiment.status == "RUNNING"
    audits = get_runtime_store().list_namespace(AUTONOMOUS_PAID_ACTIVATION_AUDIT_NAMESPACE)
    assert audits[-1]["exact_budget_cap"] == 200
    assert audits[-1]["authorization_id"] == str(result.authorization_id)


def test_running_paid_budget_is_reserved_against_daily_autonomous_cap() -> None:
    product_id = _product()
    first_action = _paid_action(product_id)
    _mandate(product_id, paid_activation=True, daily_cap=300)
    fake = FakeMetaActivationBoundary()
    coordinator = _coordinator(fake)

    first = coordinator.activate_staged(
        mandate=growth_mandate_service.get(UUID(product_id)),
        action_id=first_action,
    )
    assert first.outcome == "ACTIVATED"
    exposure = coordinator.budget_exposure(UUID(product_id))
    assert exposure.reserved_running_paid_budget == 200

    second_action = _paid_action(product_id)
    second = coordinator.activate_staged(
        mandate=growth_mandate_service.get(UUID(product_id)),
        action_id=second_action,
    )

    assert second.outcome == "BLOCKED"
    assert any("reserved RUNNING paid budgets" in reason for reason in second.reasons)
    assert len(fake.authorize_calls) == 1
    assert len(fake.activate_calls) == 1
    second_plan = distribution_execution_service.get_plan(second_action)
    assert second_plan.action.status == "APPROVED"
    assert second_plan.experiment.status == "APPROVED"


def test_mandate_pause_after_authorization_prevents_provider_activation() -> None:
    product_id = _product()
    product_uuid = UUID(product_id)
    action_id = _paid_action(product_id)
    _mandate(product_id, paid_activation=True)
    fake = FakeMetaActivationBoundary(pause_after_authorize=product_uuid)

    result = _coordinator(fake).activate_staged(
        mandate=growth_mandate_service.get(product_uuid),
        action_id=action_id,
    )

    assert result.outcome == "BLOCKED"
    assert result.authorization_id is not None
    assert len(fake.authorize_calls) == 1
    assert fake.activate_calls == []
    assert growth_mandate_service.get(product_uuid).status == GrowthMandateStatus.PAUSED
    assert any("unattempted authorization" in reason for reason in result.reasons)
    plan = distribution_execution_service.get_plan(action_id)
    assert plan.action.status == "APPROVED"
    assert plan.experiment.status == "APPROVED"
