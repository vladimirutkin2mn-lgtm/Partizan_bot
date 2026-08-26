from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.autonomy_service import growth_mandate_service
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_service import distribution_play_service
from app.execution_adapters import (
    ConfirmedMockExecutionAdapter,
    DistributionExecutionAdapterService,
    ExecutionAdapterRegistry,
)
from app.growth_autoresearch import growth_autoresearch_service
from app.growth_autoresearch_execution_runtime import (
    ResumableGrowthAutoResearchExecutionService,
)
from app.growth_autoresearch_schemas import (
    GrowthResearchBaselineRequest,
    GrowthResearchChallengerRequest,
    GrowthResearchEvidence,
    GrowthResearchPolicyRequest,
    GrowthVariantSpec,
)
from app.icp_service import icp_service
from app.main import app
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
    growth_mandate_service.reset()
    growth_autoresearch_service.reset()


def _ready_product() -> UUID:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized readings available on demand.\n"
                "Market: US\nLanguage: English\nBudget: 200\nMax CAC: 12\n"
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
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert plays.status_code == 200
    assert any(
        item["tactic_id"] == "tiktok_partizan_organic_video" and item["status"] == "READY"
        for item in plays.json()["plays"]
    )

    mandate = client.put(
        f"/v1/products/{product_id}/growth-mandate",
        json={
            "total_budget_cap": 200,
            "target_max_cac": 12,
            "max_autonomous_spend_per_experiment": 50,
            "max_autonomous_spend_per_day": 100,
            "max_concurrent_running_experiments": 2,
            "allowed_platforms": ["TIKTOK"],
            "allowed_actions": ["ORGANIC_VIDEO"],
            "autonomous_prepare": True,
            "autonomous_approve": True,
            "autonomous_paid_activation": False,
            "approval_threshold": 50,
        },
    )
    assert mandate.status_code == 200
    return UUID(product_id)


def _policy(*, paused: bool) -> GrowthResearchPolicyRequest:
    return GrowthResearchPolicyRequest(
        allowed_platforms=["TIKTOK"],
        max_changed_dimensions=2,
        max_shadow_trial_budget=10,
        shadow_research_budget=40,
        max_trial_budget_share=0.5,
        paused=paused,
    )


@pytest.mark.asyncio
async def test_paused_ready_trial_resumes_without_losing_execution_link() -> None:
    product_id = _ready_product()
    growth_autoresearch_service.configure_policy(product_id, _policy(paused=False))
    baseline = growth_autoresearch_service.establish_baseline(
        product_id,
        GrowthResearchBaselineRequest(
            variant=GrowthVariantSpec(
                platform="TIKTOK",
                tactic_id="tiktok_partizan_organic_video",
                audience="US relationship-content audience",
                message_angle="Baseline relationship clarity angle",
                test_budget=0,
            ),
            evidence=GrowthResearchEvidence(
                spend=100,
                visits=300,
                signups=30,
                activated_users=20,
                paid_users=10,
                revenue=500,
                source="measured-replay",
            ),
        ),
    )
    trial = growth_autoresearch_service.create_challenger(
        product_id,
        GrowthResearchChallengerRequest(
            variant=GrowthVariantSpec(
                platform="TIKTOK",
                tactic_id="tiktok_partizan_organic_video",
                audience="US relationship-content audience",
                message_angle="Replace uncertainty with one concrete reflection question",
                test_budget=0,
            ),
        ),
    )
    growth_autoresearch_service.configure_policy(product_id, _policy(paused=True))

    adapter_service = DistributionExecutionAdapterService(
        registry=ExecutionAdapterRegistry([ConfirmedMockExecutionAdapter()]),
        store=get_runtime_store(),
    )
    bridge = ResumableGrowthAutoResearchExecutionService(
        store=get_runtime_store(),
        adapter_service=adapter_service,
    )

    paused = await bridge.execute_trial(trial.id)

    assert paused.status == "PAUSED"
    assert paused.action_id is None
    assert distribution_execution_service.list_experiments(product_id) == []
    assert growth_autoresearch_service.get_trial(trial.id).status == "READY"

    growth_autoresearch_service.configure_policy(product_id, _policy(paused=False))
    resumed = await bridge.execute_trial(trial.id)

    assert resumed.id == paused.id
    assert resumed.status == "EXECUTED"
    assert resumed.action_id is not None
    assert resumed.experiment_id is not None
    assert resumed.proposed_spend == 0
    assert growth_autoresearch_service.current_champion(product_id).id == baseline.id
