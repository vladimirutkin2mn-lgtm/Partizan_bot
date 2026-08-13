from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.autonomous_creative_paid_growth import AutonomousCreativePaidGrowthSweepService
from app.autonomous_paid import (
    AutonomousPaidActivationOutcome,
    AutonomousPaidActivationResult,
)
from app.autonomy_service import growth_mandate_service
from app.channel_service import channel_service
from app.creative_assets import CreativeReadinessStatus, creative_asset_service
from app.creative_generation import (
    CreativeGenerationService,
    DeterministicMockCreativeGenerator,
)
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_service import distribution_play_service
from app.execution_adapters import (
    AdapterExecutionOutcome,
    DistributionAdapterExecutionView,
    ExecutionAdapterReceipt,
)
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.main import app
from app.paid_campaign import paid_campaign_spec_service
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store

client = TestClient(app)


class CountingStagingService:
    def __init__(self) -> None:
        self.calls: list[UUID] = []

    def execute(self, action_id, payload):
        readiness = creative_asset_service.readiness(action_id)
        assert readiness.status == CreativeReadinessStatus.READY
        self.calls.append(action_id)
        receipt = ExecutionAdapterReceipt(
            action_id=action_id,
            adapter_name="test-paid-staging",
            provider="test-provider",
            outcome=AdapterExecutionOutcome.STAGED,
            message="Provider objects staged without spend.",
            external_reference=f"test:staged:{action_id}",
            metadata={"spend_started": False},
            created_at=datetime.now(UTC),
        )
        return DistributionAdapterExecutionView(
            receipt=receipt,
            plan=distribution_execution_service.get_plan(action_id),
        )


class WaitingPaidCoordinator:
    def __init__(self) -> None:
        self.calls: list[UUID] = []

    def activate_staged(self, *, mandate, action_id):
        self.calls.append(action_id)
        spec = paid_campaign_spec_service.get(action_id)
        assert spec is not None
        return AutonomousPaidActivationResult(
            outcome=AutonomousPaidActivationOutcome.REQUIRE_APPROVAL,
            exact_budget_cap=spec.budget_cap,
            reasons=["Paid activation is not delegated in this test."],
        )


class RetireBeforeStagingMandateService:
    def __init__(self) -> None:
        self.evaluations = 0
        self.retired = False

    def evaluate(self, product_id, payload):
        self.evaluations += 1
        result = growth_mandate_service.evaluate(product_id, payload)
        if self.evaluations == 3 and not self.retired:
            experiments = distribution_execution_service.list_experiments(product_id)
            pending = next(
                experiment
                for experiment in experiments
                if experiment.status.value in {"DRAFT", "APPROVED"}
            )
            readiness = creative_asset_service.readiness(pending.action_id)
            assert readiness.status == CreativeReadinessStatus.READY
            assert readiness.selected_asset is not None
            creative_asset_service.retire(readiness.selected_asset.id)
            self.retired = True
        return result

    def get(self, product_id):
        return growth_mandate_service.get(product_id)


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
    distribution_analytics_service.reset()
    distribution_growth_manager_service.reset()
    paid_campaign_spec_service.reset()
    creative_asset_service.reset()
    growth_mandate_service.reset()


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
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert plays.status_code == 200
    assert any(
        play["tactic_id"] == "instagram_ads" and play["status"] == "READY"
        for play in plays.json()["plays"]
    )
    return product_id


def _set_paid_mandate(product_id: str) -> None:
    response = client.put(
        f"/v1/products/{product_id}/growth-mandate",
        json={
            "total_budget_cap": 400,
            "target_max_cac": 12,
            "max_autonomous_spend_per_experiment": 200,
            "max_autonomous_spend_per_day": 300,
            "max_concurrent_running_experiments": 2,
            "allowed_platforms": ["INSTAGRAM"],
            "allowed_actions": ["PAID_CAMPAIGN"],
            "autonomous_prepare": True,
            "autonomous_approve": True,
            "autonomous_paid_activation": False,
            "approval_threshold": 200,
        },
    )
    assert response.status_code == 200


def _service(*, generation_service, mandate_service=None):
    staging = CountingStagingService()
    paid = WaitingPaidCoordinator()
    service = AutonomousCreativePaidGrowthSweepService(
        store=get_runtime_store(),
        mandate_service=mandate_service,
        adapter_service=staging,
        paid_coordinator=paid,
        generation_service=generation_service,
    )
    return service, staging, paid


@pytest.mark.asyncio
async def test_missing_creative_waits_on_same_prepared_experiment_without_provider_calls() -> None:
    product_id = _create_product()
    _set_paid_mandate(product_id)
    service, staging, paid = _service(
        generation_service=CreativeGenerationService(),
    )

    first = await service.run_once(product_id=UUID(product_id))

    assert first.unavailable_count == 1
    decision = first.decisions[0]
    assert decision.outcome == "UNAVAILABLE"
    assert decision.action_id is not None
    first_action_id = decision.action_id
    plan = distribution_execution_service.get_plan(first_action_id)
    assert plan.action.status == "PREPARED"
    assert plan.experiment.status == "DRAFT"
    assert staging.calls == []
    assert paid.calls == []

    second = await service.run_once(product_id=UUID(product_id))

    assert second.unavailable_count == 1
    assert second.decisions[0].action_id == first_action_id
    assert len(distribution_execution_service.list_experiments(UUID(product_id))) == 1
    assert staging.calls == []
    assert paid.calls == []


@pytest.mark.asyncio
async def test_ready_generated_creative_is_required_before_approve_and_staging() -> None:
    product_id = _create_product()
    _set_paid_mandate(product_id)
    service, staging, paid = _service(
        generation_service=CreativeGenerationService(
            generator=DeterministicMockCreativeGenerator()
        ),
    )

    result = await service.run_once(product_id=UUID(product_id))

    assert result.waiting_approval_count == 1
    decision = result.decisions[0]
    assert decision.adapter_outcome == "STAGED"
    assert decision.action_id is not None
    readiness = creative_asset_service.readiness(decision.action_id)
    assert readiness.status == CreativeReadinessStatus.READY
    assert readiness.selected_asset is not None
    assert staging.calls == [decision.action_id]
    assert paid.calls == [decision.action_id]
    plan = distribution_execution_service.get_plan(decision.action_id)
    assert plan.action.status == "APPROVED"
    assert plan.experiment.status == "APPROVED"


@pytest.mark.asyncio
async def test_asset_retired_after_approve_blocks_provider_and_recovers_same_experiment() -> None:
    product_id = _create_product()
    _set_paid_mandate(product_id)
    mandate_service = RetireBeforeStagingMandateService()
    service, staging, paid = _service(
        generation_service=CreativeGenerationService(
            generator=DeterministicMockCreativeGenerator()
        ),
        mandate_service=mandate_service,
    )

    first = await service.run_once(product_id=UUID(product_id))

    assert first.blocked_count == 1
    decision = first.decisions[0]
    assert "Creative readiness changed before provider staging" in decision.reasons[0]
    assert decision.action_id is not None
    first_action_id = decision.action_id
    plan = distribution_execution_service.get_plan(first_action_id)
    assert plan.action.status == "APPROVED"
    assert plan.experiment.status == "APPROVED"
    assert staging.calls == []
    assert paid.calls == []

    second = await service.run_once(product_id=UUID(product_id))

    assert second.waiting_approval_count == 1
    assert second.decisions[0].action_id == first_action_id
    assert staging.calls == [first_action_id]
    assert paid.calls == [first_action_id]
    assert len(distribution_execution_service.list_experiments(UUID(product_id))) == 1
