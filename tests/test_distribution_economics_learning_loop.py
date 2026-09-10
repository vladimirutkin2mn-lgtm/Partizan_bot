from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_analytics_service import (
    MANAGED_ASSIGNMENT_NAMESPACE,
    distribution_analytics_service,
)
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_service import distribution_play_service
from app.icp_service import icp_service
from app.main import app
from app.managed_distribution import managed_distribution_service
from app.opportunity_enrichment import opportunity_enrichment_service
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
    managed_distribution_service.reset()


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
                "Budget: 500\n"
                "Max CAC: 10\n"
                "Goal: Acquire 100 paid users"
            )
        },
    )
    assert response.status_code == 201
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert plays.status_code == 200
    return product_id


def _play(product_id: str, tactic_id: str) -> dict:
    response = client.get(f"/v1/products/{product_id}/distribution-plays")
    assert response.status_code == 200
    return next(item for item in response.json()["plays"] if item["tactic_id"] == tactic_id)


def _run_experiment(product_id: str, tactic_id: str = "instagram_ads") -> dict:
    play = _play(product_id, tactic_id)
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert prepared.status_code == 200, prepared.text
    action_id = prepared.json()["action"]["id"]
    assert client.post(f"/v1/distribution-actions/{action_id}/approve").status_code == 200
    executed = client.post(
        f"/v1/distribution-actions/{action_id}/mark-executed",
        json={"external_reference": f"{tactic_id}-campaign"},
    )
    assert executed.status_code == 200, executed.text
    return executed.json()


def _spend(experiment_id: str, amount: float, **fields) -> dict:
    payload = {"spend_id": str(uuid4()), "amount": amount, **fields}
    response = client.post(
        f"/v1/distribution-experiments/{experiment_id}/spend",
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _event(experiment_id: str, event_type: str, **fields) -> dict:
    payload = {
        "event_id": str(uuid4()),
        "experiment_id": experiment_id,
        "event_type": event_type,
        **fields,
    }
    response = client.post("/v1/distribution-analytics/events", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_cost_categories_keep_internal_operating_cost_out_of_customer_economics() -> None:
    product_id = _product()
    plan = _run_experiment(product_id)
    experiment_id = plan["experiment"]["id"]

    _spend(experiment_id, 20)
    _spend(experiment_id, 5, category="RESEARCH_FEE")
    _spend(experiment_id, 10, category="EXECUTION_FEE")
    _spend(
        experiment_id,
        7,
        category="OPERATING_COST",
        evidence_kind="OBSERVED",
    )
    _spend(
        experiment_id,
        30,
        category="OPERATING_COST",
        evidence_kind="ESTIMATE",
    )
    _spend(
        experiment_id,
        99,
        category="OPERATING_COST",
        evidence_kind="SYNTHETIC",
    )

    analytics = client.get(f"/v1/distribution-experiments/{experiment_id}/analytics")
    assert analytics.status_code == 200
    body = analytics.json()
    assert body["metrics"]["spend"] == 35
    assert body["costs"] == {
        "research_fee": 5.0,
        "execution_fee": 10.0,
        "distribution_spend": 20.0,
        "operating_cost": 136.0,
        "customer_total": 35.0,
    }
    assert body["publisher_mode"] == "MANUAL"

    customer = distribution_analytics_service.customer_economics(UUID(product_id)).model_dump()
    assert customer["costs"] == {
        "research_fee": 5.0,
        "execution_fee": 10.0,
        "distribution_spend": 20.0,
        "total": 35.0,
    }
    assert "operating_cost" not in customer["costs"]

    pricing = client.get(f"/v1/products/{product_id}/distribution-pricing-assumptions")
    assert pricing.status_code == 200
    assert pricing.json() == [
        {
            "platform": "INSTAGRAM",
            "action_type": plan["action"]["action_type"],
            "publisher_mode": "MANUAL",
            "observed_operating_cost": 7.0,
            "sample_count": 1,
            "updated_at": pricing.json()[0]["updated_at"],
        }
    ]


def test_managed_fulfillment_costs_are_reused_without_customer_internal_cost_leakage() -> None:
    product_id = _product()
    plan = _run_experiment(product_id)
    action_id = UUID(plan["action"]["id"])
    experiment_id = plan["experiment"]["id"]
    now = datetime.now(UTC)

    distribution_execution_service.record_external_observation(
        action_id,
        provider="partizan_managed",
        observation={"assignment_id": str(uuid4()), "fulfilled_at": now.isoformat()},
    )
    get_runtime_store().put(
        MANAGED_ASSIGNMENT_NAMESPACE,
        str(uuid4()),
        {
            "action_id": str(action_id),
            "status": "FULFILLED",
            "platform": plan["action"]["platform"],
            "action_type": plan["action"]["action_type"],
            "fulfilled_at": now.isoformat(),
            "cost": {
                "distribution_spend_usd": 2.0,
                "operational_cost_usd": 5.0,
                "management_fee_usd": 3.0,
            },
        },
    )

    analytics = client.get(f"/v1/distribution-experiments/{experiment_id}/analytics")
    assert analytics.status_code == 200
    body = analytics.json()
    assert body["publisher_mode"] == "PARTIZAN_MANAGED"
    assert body["metrics"]["spend"] == 5.0
    assert body["costs"]["distribution_spend"] == 2.0
    assert body["costs"]["execution_fee"] == 3.0
    assert body["costs"]["operating_cost"] == 5.0
    assert body["costs"]["customer_total"] == 5.0

    pricing = client.get(f"/v1/products/{product_id}/distribution-pricing-assumptions")
    assert pricing.status_code == 200
    assert len(pricing.json()) == 1
    assumption = pricing.json()[0]
    assert assumption["publisher_mode"] == "PARTIZAN_MANAGED"
    assert assumption["observed_operating_cost"] == 5.0
    assert assumption["sample_count"] == 1


def test_reply_and_removal_outcomes_stop_pattern_and_persist_learning() -> None:
    product_id = _product()
    plan = _run_experiment(product_id)
    experiment_id = plan["experiment"]["id"]

    _event(experiment_id, "REPLY", properties={"count": 4})
    _event(experiment_id, "REMOVED")

    analytics = client.get(f"/v1/distribution-experiments/{experiment_id}/analytics")
    assert analytics.status_code == 200
    assert analytics.json()["replies"] == 4
    assert analytics.json()["removals"] == 1

    decision = client.post(f"/v1/distribution-experiments/{experiment_id}/growth-decision")
    assert decision.status_code == 200
    decision_body = decision.json()
    assert decision_body["action"] == "STOP"
    assert decision_body["replies"] == 4
    assert decision_body["removals"] == 1
    assert decision_body["publisher_mode"] == "MANUAL"
    assert decision_body["action_type"] == plan["action"]["action_type"]
    assert any("removed" in item.lower() for item in decision_body["rationale"])

    memory = client.get(f"/v1/products/{product_id}/distribution-learning")
    assert memory.status_code == 200
    entries = memory.json()["entries"]
    learned = next(item for item in entries if item["experiment_id"] == experiment_id)
    assert learned["action"] == "STOP"
    assert learned["replies"] == 4
    assert learned["removals"] == 1
    assert learned["publisher_mode"] == "MANUAL"

    assert client.post(f"/v1/distribution-experiments/{experiment_id}/finish").status_code == 200
    portfolio = client.get(f"/v1/products/{product_id}/distribution-portfolio?max_items=12")
    assert portfolio.status_code == 200
    tactic_id = plan["action"]["operational_metadata"]["tactic_id"]
    same_tactic = next(
        item
        for item in portfolio.json()["items"]
        if item["play"]["tactic_id"] == tactic_id
    )
    assert any("Community/action penalty" in item for item in same_tactic["rationale"])


def test_reddit_research_prior_outcomes_reward_replies_and_penalize_removals(monkeypatch) -> None:
    product_id = uuid4()
    opportunity_id = uuid4()

    def analytics_for(*, replies: int, removals: int):
        return SimpleNamespace(
            experiments=[
                SimpleNamespace(
                    play=SimpleNamespace(opportunity_id=opportunity_id),
                    metrics=SimpleNamespace(
                        visits=0,
                        signups=0,
                        activated_users=0,
                        paid_users=0,
                        revenue=0.0,
                    ),
                    replies=replies,
                    removals=removals,
                )
            ]
        )

    monkeypatch.setattr(
        distribution_analytics_service,
        "product_analytics",
        lambda _: analytics_for(replies=0, removals=0),
    )
    baseline = opportunity_enrichment_service._prior_outcome_score(product_id, opportunity_id)

    monkeypatch.setattr(
        distribution_analytics_service,
        "product_analytics",
        lambda _: analytics_for(replies=4, removals=0),
    )
    replied = opportunity_enrichment_service._prior_outcome_score(product_id, opportunity_id)

    monkeypatch.setattr(
        distribution_analytics_service,
        "product_analytics",
        lambda _: analytics_for(replies=4, removals=1),
    )
    removed = opportunity_enrichment_service._prior_outcome_score(product_id, opportunity_id)

    assert baseline == 40.0
    assert replied > baseline
    assert removed < baseline
