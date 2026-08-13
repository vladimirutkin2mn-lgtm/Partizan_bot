from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.autonomous_owned_creative_growth import AutonomousOwnedCreativeGrowthSweepService
from app.autonomy_service import growth_mandate_service
from app.channel_service import channel_service
from app.creative_assets import (
    CreativeAssetRegisterRequest,
    CreativeAssetSource,
    CreativeAssetStatus,
    CreativeReadinessStatus,
    creative_asset_service,
)
from app.creative_execution_adapters import (
    CREATIVE_EXECUTION_ATTRIBUTION_NAMESPACE,
    CreativeExecutionAttributionView,
)
from app.creative_generation import (
    CreativeGenerationOutcome,
    CreativeGenerationView,
)
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_service import distribution_play_service
from app.execution_adapters import (
    AdapterExecutionOutcome,
    DistributionAdapterExecutionView,
    DistributionAdapterExecuteRequest,
    DistributionExecutionAdapterService,
    ExecutionAdapterReceipt,
    ExecutionAdapterRegistry,
)
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.main import app
from app.organic_creative_execution import OrganicVideoCreativeExecutionAdapter
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store

client = TestClient(app)


class CurrentReadinessGenerationService:
    def __init__(self, outcome: CreativeGenerationOutcome) -> None:
        self.outcome = outcome
        self.calls: list[UUID] = []

    def ensure_ready(self, action_id: UUID) -> CreativeGenerationView:
        self.calls.append(action_id)
        readiness = creative_asset_service.readiness(action_id)
        return CreativeGenerationView(
            action_id=action_id,
            outcome=self.outcome,
            brief=readiness.brief,
            asset=readiness.selected_asset,
            readiness=readiness,
            message=(
                "test creative ready"
                if self.outcome == CreativeGenerationOutcome.READY
                else "test creative unavailable"
            ),
        )


class ResumeExecutionService:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, bool]] = []

    def execute(self, action_id: UUID, payload: DistributionAdapterExecuteRequest):
        self.calls.append((action_id, payload.retry))
        return DistributionAdapterExecutionView(
            receipt=ExecutionAdapterReceipt(
                action_id=action_id,
                adapter_name="resume-test",
                provider="operator-consent-boundary",
                outcome=AdapterExecutionOutcome.UNAVAILABLE,
                message="Publishing still requires explicit creator consent.",
                requires_operator_confirmation=True,
                created_at=datetime.now(UTC),
            ),
            plan=distribution_execution_service.get_plan(action_id),
        )


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
    creative_asset_service.reset()
    growth_mandate_service.reset()
    get_runtime_store().clear_namespace(CREATIVE_EXECUTION_ATTRIBUTION_NAMESPACE)


def _product_and_organic_play() -> tuple[str, dict]:
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
    organic = next(
        play for play in plays.json()["plays"] if play["action_type"] == "ORGANIC_VIDEO"
    )
    return product_id, organic


def _approved_organic_action(product_id: str, play: dict) -> str:
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert prepared.status_code == 200
    action_id = prepared.json()["action"]["id"]
    approved = client.post(f"/v1/distribution-actions/{action_id}/approve")
    assert approved.status_code == 200
    return action_id


def _service(generation_service) -> DistributionExecutionAdapterService:
    adapter = OrganicVideoCreativeExecutionAdapter(
        generation_service=generation_service,
        attribution_store=get_runtime_store(),
    )
    return DistributionExecutionAdapterService(
        registry=ExecutionAdapterRegistry([adapter]),
        store=get_runtime_store(),
    )


def test_organic_execution_stops_at_creative_gate_when_video_is_unavailable() -> None:
    product_id, play = _product_and_organic_play()
    action_id = _approved_organic_action(product_id, play)
    generation = CurrentReadinessGenerationService(CreativeGenerationOutcome.UNAVAILABLE)
    service = _service(generation)

    result = service.execute(UUID(action_id), DistributionAdapterExecuteRequest())

    assert result.receipt.outcome == AdapterExecutionOutcome.UNAVAILABLE
    assert result.receipt.adapter_name == "owned-organic-video-creative-gate"
    assert result.receipt.metadata["creative_readiness"] == "BLOCKED"
    assert generation.calls == [UUID(action_id)]
    assert result.plan.action.status.value == "APPROVED"
    assert result.plan.experiment.status.value == "APPROVED"
    assert get_runtime_store().get(
        CREATIVE_EXECUTION_ATTRIBUTION_NAMESPACE,
        action_id,
    ) is None

    retried = service.execute(
        UUID(action_id),
        DistributionAdapterExecuteRequest(retry=True),
    )
    assert retried.receipt.outcome == AdapterExecutionOutcome.UNAVAILABLE
    assert generation.calls == [UUID(action_id), UUID(action_id)]


def test_ready_organic_video_reaches_consent_boundary_with_asset_attribution() -> None:
    product_id, play = _product_and_organic_play()
    action_id = _approved_organic_action(product_id, play)
    brief = creative_asset_service.ensure_brief(UUID(action_id))
    asset = creative_asset_service.register_asset(
        CreativeAssetRegisterRequest(
            brief_id=brief.id,
            source=CreativeAssetSource.EXTERNAL_URL,
            status=CreativeAssetStatus.READY,
            public_url="https://cdn.example.com/oracle-organic.mp4",
            mime_type="video/mp4",
            width=720,
            height=1280,
            duration_seconds=8,
            provenance={"generator": "test-video-source"},
        )
    )
    readiness = creative_asset_service.readiness(UUID(action_id))
    assert readiness.status == CreativeReadinessStatus.READY
    generation = CurrentReadinessGenerationService(CreativeGenerationOutcome.READY)
    service = _service(generation)

    result = service.execute(UUID(action_id), DistributionAdapterExecuteRequest())

    assert result.receipt.outcome == AdapterExecutionOutcome.UNAVAILABLE
    assert result.receipt.requires_operator_confirmation is True
    assert result.receipt.metadata["creative_source"] == "ACTION_ASSET"
    assert result.receipt.metadata["creative_asset_id"] == str(asset.id)
    assert "explicit-consent" in result.receipt.message
    attribution_payload = get_runtime_store().get(
        CREATIVE_EXECUTION_ATTRIBUTION_NAMESPACE,
        action_id,
    )
    assert attribution_payload is not None
    attribution = CreativeExecutionAttributionView.model_validate(attribution_payload)
    assert attribution.asset_id == asset.id
    assert attribution.brief_fingerprint == brief.fingerprint
    assert attribution.adapter_outcome == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_autonomous_worker_resumes_same_approved_organic_action_with_retry() -> None:
    product_id, play = _product_and_organic_play()
    action_id = _approved_organic_action(product_id, play)
    mandate = client.put(
        f"/v1/products/{product_id}/growth-mandate",
        json={
            "total_budget_cap": 200,
            "target_max_cac": 12,
            "max_autonomous_spend_per_experiment": 50,
            "max_autonomous_spend_per_day": 100,
            "max_concurrent_running_experiments": 2,
            "allowed_platforms": [play["platform"]],
            "allowed_actions": ["ORGANIC_VIDEO"],
            "autonomous_prepare": True,
            "autonomous_approve": True,
            "autonomous_paid_activation": False,
            "approval_threshold": 50,
        },
    )
    assert mandate.status_code == 200
    resume = ResumeExecutionService()
    worker = AutonomousOwnedCreativeGrowthSweepService(
        store=get_runtime_store(),
        adapter_service=resume,  # type: ignore[arg-type]
    )

    result = await worker.run_once(product_id=UUID(product_id))

    assert result.unavailable_count == 1
    assert result.decisions[0].action_id == UUID(action_id)
    assert resume.calls == [(UUID(action_id), True)]
    assert len(distribution_execution_service.list_experiments(UUID(product_id))) == 1
