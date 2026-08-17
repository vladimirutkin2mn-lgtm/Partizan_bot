from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_schemas import DistributionTacticClass
from app.distribution_play_service import distribution_play_service
from app.growth_planning import (
    GROWTH_PLANNING_SKILLS,
    MAX_PLANNING_ADJUSTMENT,
    GrowthPlanningEngine,
)
from app.icp_service import icp_service
from app.main import app
from app.product_intake import product_intake_service

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
                "Budget: 500\n"
                "Max CAC: 10\n"
                "Goal: Acquire paid users"
            )
        },
    )
    assert response.status_code == 201
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    assert client.post(
        f"/v1/products/{product_id}/distribution-plays/generate"
    ).status_code == 200
    return product_id


def test_growth_planning_methodology_rewards_evidence_speed_and_low_cost() -> None:
    engine = GrowthPlanningEngine()
    strong = SimpleNamespace(
        estimated_cost_min=0,
        estimated_cost_max=10,
        time_to_signal_days=2,
        effort_hours=0.5,
        tactic_class=DistributionTacticClass.COMMUNITY,
    )
    weak = SimpleNamespace(
        estimated_cost_min=50,
        estimated_cost_max=90,
        time_to_signal_days=45,
        effort_hours=12,
        tactic_class=DistributionTacticClass.PAID_PLATFORM,
    )

    strong_assessment = engine.assess(
        strong,
        budget_remaining=100,
        research_signals={
            "confidence": "HIGH",
            "independent_evidence_count": 3,
            "demand_intent_hits": 2,
            "commercial_intent_hits": 1,
        },
    )
    weak_assessment = engine.assess(
        weak,
        budget_remaining=100,
        research_signals={
            "confidence": "LOW",
            "independent_evidence_count": 1,
            "demand_intent_hits": 0,
            "commercial_intent_hits": 0,
        },
    )

    assert GROWTH_PLANNING_SKILLS == (
        "marketing-ideas",
        "customer-research",
        "prospecting",
    )
    assert strong_assessment.feasible is True
    assert weak_assessment.feasible is True
    assert strong_assessment.adjustment > weak_assessment.adjustment
    assert abs(strong_assessment.adjustment) <= MAX_PLANNING_ADJUSTMENT
    assert abs(weak_assessment.adjustment) <= MAX_PLANNING_ADJUSTMENT
    assert "observed experiment economics remain authoritative" in " ".join(
        strong_assessment.rationale
    )


def test_growth_planning_rejects_play_that_cannot_fit_remaining_budget() -> None:
    engine = GrowthPlanningEngine()
    play = SimpleNamespace(
        estimated_cost_min=120,
        estimated_cost_max=150,
        time_to_signal_days=7,
        effort_hours=2,
        tactic_class=DistributionTacticClass.PAID_PLATFORM,
    )

    assessment = engine.assess(
        play,
        budget_remaining=100,
        research_signals={"confidence": "HIGH"},
    )

    assert assessment.feasible is False
    assert assessment.adjustment == -MAX_PLANNING_ADJUSTMENT
    assert "do not sequence this play yet" in " ".join(assessment.rationale)


def test_distribution_portfolio_is_runtime_wired_to_growth_planning() -> None:
    product_id = _create_product()

    response = client.get(
        f"/v1/products/{product_id}/distribution-portfolio?max_items=12"
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert items
    for item in items:
        rationale = " ".join(item["rationale"])
        assert "Growth planning skills: marketing-ideas, customer-research, prospecting." in rationale
        assert "Growth planning adjustment=" in rationale
        assert item["recommended_budget_cap"] >= item["play"]["estimated_cost_min"]
