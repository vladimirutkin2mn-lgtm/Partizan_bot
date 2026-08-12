from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.autonomous_growth import (
    AUTONOMOUS_GROWTH_DECISION_NAMESPACE,
    AUTONOMOUS_GROWTH_RUN_NAMESPACE,
    AutonomousGrowthSweepService,
)
from app.autonomous_growth_worker import AutonomousGrowthWorker
from app.autonomy_service import growth_mandate_service
from app.channel_service import channel_service
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
    distribution_analytics_service.reset()
    distribution_growth_manager_service.reset()
    growth_mandate_service.reset()
    store = get_runtime_store()
    if store.ephemeral:
        store.clear_namespace(AUTONOMOUS_GROWTH_RUN_NAMESPACE)
        store.clear_namespace(AUTONOMOUS_GROWTH_DECISION_NAMESPACE)


def _create_product(name: str = "Oracle") -> str:
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


def _add_tiktok_organic_identity(product_id: str) -> str:
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
    identity_id = identity.json()["id"]
    slot = client.post(
        f"/v1/products/{product_id}/campaign-slots",
        json={
            "distribution_identity_id": identity_id,
            "status": "ACTIVE",
            "attribution_route": "https://example.com/oracle",
        },
    )
    assert slot.status_code == 201
    return identity_id


def _generate_plays(product_id: str) -> list[dict]:
    response = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert response.status_code == 200
    return response.json()["plays"]


def _set_mandate(
    product_id: str,
    *,
    platforms: list[str],
    actions: list[str],
    autonomous_approve: bool = True,
    max_concurrent: int = 1,
) -> dict:
    response = client.put(
        f"/v1/products/{product_id}/growth-mandate",
        json={
            "total_budget_cap": 200,
            "target_max_cac": 12,
            "max_autonomous_spend_per_experiment": 50,
            "max_autonomous_spend_per_day": 100,
            "max_concurrent_running_experiments": max_concurrent,
            "allowed_platforms": platforms,
            "allowed_actions": actions,
            "autonomous_prepare": True,
            "autonomous_approve": autonomous_approve,
            "autonomous_paid_activation": False,
            "approval_threshold": 50,
        },
    )
    assert response.status_code == 200
    return response.json()


def _mock_worker_service() -> AutonomousGrowthSweepService:
    adapter_service = DistributionExecutionAdapterService(
        registry=ExecutionAdapterRegistry([ConfirmedMockExecutionAdapter()]),
        store=get_runtime_store(),
    )
    return AutonomousGrowthSweepService(
        store=get_runtime_store(),
        adapter_service=adapter_service,
    )


@pytest.mark.asyncio
async def test_worker_executes_one_non_paid_action_inside_mandate() -> None:
    product_id = _create_product()
    _add_tiktok_organic_identity(product_id)
    plays = _generate_plays(product_id)
    assert any(
        play["tactic_id"] == "tiktok_partizan_organic_video"
        and play["status"] == "READY"
        for play in plays
    )
    mandate = _set_mandate(
        product_id,
        platforms=["TIKTOK"],
        actions=["ORGANIC_VIDEO"],
    )
    service = _mock_worker_service()

    result = await service.run_once(product_id=UUID(product_id))

    assert result.product_count == 1
    assert result.executed_count == 1
    decision = next(item for item in result.decisions if item.outcome == "EXECUTED")
    assert str(decision.mandate_id) == mandate["id"]
    assert decision.mandate_version == mandate["version"]
    assert decision.action_id is not None
    assert decision.experiment_id is not None
    plan = distribution_execution_service.get_plan(decision.action_id)
    assert plan.action.status == "EXECUTED"
    assert plan.experiment.status == "RUNNING"

    recent = service.recent_runs(1)
    assert recent[0].run_id == result.run_id

    second = await service.run_once(product_id=UUID(product_id))
    assert second.executed_count == 0
    assert second.blocked_count >= 1
    experiments = distribution_execution_service.list_experiments(UUID(product_id))
    assert len(experiments) == 1


@pytest.mark.asyncio
async def test_worker_prepares_but_waits_when_approval_is_not_delegated() -> None:
    product_id = _create_product()
    _add_tiktok_organic_identity(product_id)
    _generate_plays(product_id)
    _set_mandate(
        product_id,
        platforms=["TIKTOK"],
        actions=["ORGANIC_VIDEO"],
        autonomous_approve=False,
    )
    service = _mock_worker_service()

    result = await service.run_once(product_id=UUID(product_id))

    assert result.executed_count == 0
    assert result.waiting_approval_count == 1
    decision = next(
        item for item in result.decisions if item.outcome == "WAITING_APPROVAL"
    )
    assert decision.action_id is not None
    plan = distribution_execution_service.get_plan(decision.action_id)
    assert plan.action.status == "PREPARED"
    assert plan.experiment.status == "DRAFT"


@pytest.mark.asyncio
async def test_worker_does_not_stage_paid_campaigns_in_first_slice() -> None:
    product_id = _create_product()
    plays = _generate_plays(product_id)
    assert any(
        play["action_type"] == "PAID_CAMPAIGN" and play["status"] == "READY"
        for play in plays
    )
    _set_mandate(
        product_id,
        platforms=["INSTAGRAM"],
        actions=["PAID_CAMPAIGN"],
    )
    service = _mock_worker_service()

    result = await service.run_once(product_id=UUID(product_id))

    assert result.executed_count == 0
    assert result.decisions[0].outcome == "SKIPPED"
    assert "non-paid" in result.decisions[0].reasons[0]
    assert distribution_execution_service.list_experiments(UUID(product_id)) == []


def test_recurring_worker_requires_durable_storage() -> None:
    worker = AutonomousGrowthWorker(sweep_service=_mock_worker_service())

    with pytest.raises(RuntimeError, match="RUNTIME_STORAGE=database"):
        worker.run(
            once=False,
            interval_seconds=300,
            max_runs=1,
            emit=lambda _: None,
        )
