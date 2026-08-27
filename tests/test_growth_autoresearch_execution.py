from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.autonomy_service import growth_mandate_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_service import distribution_play_service
from app.execution_adapters import (
    ConfirmedMockExecutionAdapter,
    DistributionExecutionAdapterService,
    ExecutionAdapterRegistry,
    UnavailableOwnedExecutionAdapter,
)
from app.growth_autoresearch import (
    GROWTH_AUTORESEARCH_CHAMPION_NAMESPACE,
    GROWTH_AUTORESEARCH_CURRENT_CHAMPION_NAMESPACE,
    GROWTH_AUTORESEARCH_EVALUATION_NAMESPACE,
    GROWTH_AUTORESEARCH_POLICY_NAMESPACE,
    GROWTH_AUTORESEARCH_TRIAL_NAMESPACE,
    growth_autoresearch_service,
)
from app.growth_autoresearch_execution import (
    GROWTH_AUTORESEARCH_EXECUTION_NAMESPACE,
    GROWTH_AUTORESEARCH_EXECUTION_TRIAL_NAMESPACE,
    GrowthAutoResearchExecutionService,
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
    store = get_runtime_store()
    if store.ephemeral:
        for namespace in (
            GROWTH_AUTORESEARCH_POLICY_NAMESPACE,
            GROWTH_AUTORESEARCH_CURRENT_CHAMPION_NAMESPACE,
            GROWTH_AUTORESEARCH_CHAMPION_NAMESPACE,
            GROWTH_AUTORESEARCH_TRIAL_NAMESPACE,
            GROWTH_AUTORESEARCH_EVALUATION_NAMESPACE,
            GROWTH_AUTORESEARCH_EXECUTION_NAMESPACE,
            GROWTH_AUTORESEARCH_EXECUTION_TRIAL_NAMESPACE,
            CUSTOMER_PROJECT_NAMESPACE,
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


def _set_mandate(product_id: str, *, platform: str, action: str) -> None:
    response = client.put(
        f"/v1/products/{product_id}/growth-mandate",
        json={
            "total_budget_cap": 200,
            "target_max_cac": 12,
            "max_autonomous_spend_per_experiment": 50,
            "max_autonomous_spend_per_day": 100,
            "max_concurrent_running_experiments": 2,
            "allowed_platforms": [platform],
            "allowed_actions": [action],
            "autonomous_prepare": True,
            "autonomous_approve": True,
            "autonomous_paid_activation": False,
            "approval_threshold": 50,
        },
    )
    assert response.status_code == 200


def _trial(
    product_id: str,
    *,
    platform: str,
    tactic_id: str,
    paused: bool = False,
):
    product_uuid = UUID(product_id)
    growth_autoresearch_service.configure_policy(
        product_uuid,
        GrowthResearchPolicyRequest(
            allowed_platforms=[platform],
            max_changed_dimensions=2,
            max_shadow_trial_budget=10,
            shadow_research_budget=40,
            max_trial_budget_share=0.5,
            paused=paused,
        ),
    )
    baseline = growth_autoresearch_service.establish_baseline(
        product_uuid,
        GrowthResearchBaselineRequest(
            variant=GrowthVariantSpec(
                platform=platform,
                tactic_id=tactic_id,
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
    if paused:
        return baseline, None
    challenger = growth_autoresearch_service.create_challenger(
        product_uuid,
        GrowthResearchChallengerRequest(
            variant=GrowthVariantSpec(
                platform=platform,
                tactic_id=tactic_id,
                audience="US relationship-content audience",
                message_angle="Replace uncertainty with one concrete reflection question",
                test_budget=0,
            ),
        ),
    )
    return baseline, challenger


def _bridge(*, adapter) -> GrowthAutoResearchExecutionService:
    adapter_service = DistributionExecutionAdapterService(
        registry=ExecutionAdapterRegistry([adapter]),
        store=get_runtime_store(),
    )
    return GrowthAutoResearchExecutionService(
        store=get_runtime_store(),
        adapter_service=adapter_service,
    )


@pytest.mark.asyncio
async def test_owned_trial_executes_through_existing_adapter_without_promoting_champion() -> None:
    product_id = _create_product()
    _add_tiktok_identity(product_id)
    plays = _generate_plays(product_id)
    assert any(
        item["tactic_id"] == "tiktok_partizan_organic_video"
        and item["status"] == "READY"
        for item in plays
    )
    _set_mandate(product_id, platform="TIKTOK", action="ORGANIC_VIDEO")
    baseline, trial = _trial(
        product_id,
        platform="TIKTOK",
        tactic_id="tiktok_partizan_organic_video",
    )
    bridge = _bridge(adapter=ConfirmedMockExecutionAdapter())

    result = await bridge.execute_trial(trial.id)

    assert result.status == "EXECUTED"
    assert result.proposed_spend == 0
    assert result.action_id is not None
    assert result.experiment_id is not None
    plan = distribution_execution_service.get_plan(result.action_id)
    assert plan.action.status == "EXECUTED"
    assert plan.experiment.status == "RUNNING"
    assert "AutoResearch trial context" in " ".join(
        distribution_play_service.find(UUID(product_id), result.play_id).rationale
        + [str(plan.action.content_payload.get("hypothesis") or "")]
    ) or "Replace uncertainty" in str(plan.action.content_payload.get("hypothesis") or "")
    assert growth_autoresearch_service.current_champion(UUID(product_id)).id == baseline.id

    restarted = GrowthAutoResearchExecutionService(store=get_runtime_store())
    restored = restarted.get_for_trial(trial.id)
    assert restored is not None
    assert restored.id == result.id
    assert restored.action_id == result.action_id
    assert len(restarted.list_for_product(UUID(product_id))) == 1


@pytest.mark.asyncio
async def test_customer_research_only_channel_blocks_live_execution() -> None:
    product_id = _create_product()
    _add_tiktok_identity(product_id)
    _generate_plays(product_id)
    _set_mandate(product_id, platform="TIKTOK", action="ORGANIC_VIDEO")
    _, trial = _trial(
        product_id,
        platform="TIKTOK",
        tactic_id="tiktok_partizan_organic_video",
    )
    project_id = uuid4()
    get_runtime_store().put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {
            "id": str(project_id),
            "product_id": product_id,
            "channel_preferences": {"TIKTOK": "RESEARCH_ONLY"},
        },
    )
    bridge = _bridge(adapter=ConfirmedMockExecutionAdapter())

    result = await bridge.execute_trial(trial.id)

    assert result.status == "BLOCKED"
    assert "not in AUTO mode" in result.reasons[0]
    assert distribution_execution_service.list_experiments(UUID(product_id)) == []


@pytest.mark.asyncio
async def test_missing_owned_adapter_is_unavailable_not_fake_execution() -> None:
    product_id = _create_product()
    _add_tiktok_identity(product_id)
    _generate_plays(product_id)
    _set_mandate(product_id, platform="TIKTOK", action="ORGANIC_VIDEO")
    baseline, trial = _trial(
        product_id,
        platform="TIKTOK",
        tactic_id="tiktok_partizan_organic_video",
    )
    bridge = _bridge(adapter=UnavailableOwnedExecutionAdapter())

    result = await bridge.execute_trial(trial.id)

    assert result.status == "UNAVAILABLE"
    assert result.adapter_outcome == "UNAVAILABLE"
    assert distribution_execution_service.get_action(result.action_id).status == "APPROVED"
    assert growth_autoresearch_service.current_champion(UUID(product_id)).id == baseline.id


@pytest.mark.asyncio
async def test_growth_mandate_denial_blocks_before_action_creation() -> None:
    product_id = _create_product()
    _add_tiktok_identity(product_id)
    _generate_plays(product_id)
    _set_mandate(product_id, platform="INSTAGRAM", action="COMMENT")
    _, trial = _trial(
        product_id,
        platform="TIKTOK",
        tactic_id="tiktok_partizan_organic_video",
    )
    bridge = _bridge(adapter=ConfirmedMockExecutionAdapter())

    result = await bridge.execute_trial(trial.id)

    assert result.status == "BLOCKED"
    assert any("not allowed by the Growth Mandate" in reason for reason in result.reasons)
    assert distribution_execution_service.list_experiments(UUID(product_id)) == []


@pytest.mark.asyncio
async def test_paid_autoresearch_trial_is_hard_blocked_even_with_paid_mandate() -> None:
    product_id = _create_product()
    plays = _generate_plays(product_id)
    assert any(
        item["tactic_id"] == "instagram_ads" and item["status"] == "READY"
        for item in plays
    )
    _set_mandate(product_id, platform="INSTAGRAM", action="PAID_CAMPAIGN")
    baseline, trial = _trial(
        product_id,
        platform="INSTAGRAM",
        tactic_id="instagram_ads",
    )
    bridge = _bridge(adapter=ConfirmedMockExecutionAdapter())

    result = await bridge.execute_trial(trial.id)

    assert result.status == "BLOCKED"
    assert "Paid campaigns are hard-blocked" in result.reasons[0]
    assert distribution_execution_service.list_experiments(UUID(product_id)) == []
    assert growth_autoresearch_service.current_champion(UUID(product_id)).id == baseline.id
