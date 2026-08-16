from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.channel_service import channel_service
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_service import distribution_play_service
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.main import app
from app.product_intake import product_intake_service

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
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert plays.status_code == 200
    return product_id


def _play(product_id: str, tactic_id: str) -> dict:
    result = client.get(f"/v1/products/{product_id}/distribution-plays")
    assert result.status_code == 200
    return next(play for play in result.json()["plays"] if play["tactic_id"] == tactic_id)


def _run_paid_experiment(product_id: str, tactic_id: str) -> dict:
    play = _play(product_id, tactic_id)
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert prepared.status_code == 200
    action_id = prepared.json()["action"]["id"]
    assert client.post(f"/v1/distribution-actions/{action_id}/approve").status_code == 200
    executed = client.post(
        f"/v1/distribution-actions/{action_id}/mark-executed",
        json={"external_reference": f"{tactic_id}-campaign"},
    )
    assert executed.status_code == 200
    return executed.json()


def _event(
    experiment: dict,
    event_type: str,
    *,
    actor_id: str | None = None,
    revenue: float = 0,
    event_id: str | None = None,
    use_referral: bool = False,
) -> dict:
    payload = {
        "event_id": event_id or str(uuid4()),
        "event_type": event_type,
        "actor_id": actor_id,
        "revenue": revenue,
    }
    if use_referral:
        payload["referral_token"] = experiment["referral_token"]
    else:
        payload["experiment_id"] = experiment["id"]
    response = client.post("/v1/distribution-analytics/events", json=payload)
    assert response.status_code == 201
    return response.json()


def _spend(experiment_id: str, amount: float, spend_id: str | None = None) -> dict:
    response = client.post(
        f"/v1/distribution-experiments/{experiment_id}/spend",
        json={"spend_id": spend_id or str(uuid4()), "amount": amount},
    )
    assert response.status_code == 201
    return response.json()


def test_event_attribution_is_idempotent_and_resolves_referral_token() -> None:
    product_id = _product()
    plan = _run_paid_experiment(product_id, "instagram_ads")
    experiment = plan["experiment"]
    event_id = str(uuid4())

    first = _event(
        experiment,
        "VISIT",
        event_id=event_id,
        use_referral=True,
    )
    duplicate = _event(
        experiment,
        "VISIT",
        event_id=event_id,
        use_referral=True,
    )

    assert first["experiment_id"] == experiment["id"]
    assert first["attributed_by"] == "referral_token"
    assert first["duplicate"] is False
    assert duplicate["duplicate"] is True


def test_distribution_metrics_and_breakdowns_include_platform_tactic_and_identity() -> None:
    product_id = _product()
    plan = _run_paid_experiment(product_id, "instagram_ads")
    experiment = plan["experiment"]
    _spend(experiment["id"], 24)
    for index in range(3):
        actor = f"paid-{index}"
        _event(experiment, "VISIT", actor_id=actor)
        _event(experiment, "SIGNUP", actor_id=actor)
        _event(experiment, "PAID", actor_id=actor, revenue=15)

    analytics = client.get(
        f"/v1/distribution-experiments/{experiment['id']}/analytics"
    )
    assert analytics.status_code == 200
    metrics = analytics.json()["metrics"]
    assert metrics["spend"] == 24
    assert metrics["paid_users"] == 3
    assert metrics["revenue"] == 45
    assert metrics["cac"] == 8
    assert metrics["roas"] == 1.875

    product = client.get(f"/v1/products/{product_id}/distribution-analytics")
    assert product.status_code == 200
    breakdowns = product.json()["breakdowns"]
    assert any(
        row["dimension"] == "PLATFORM" and row["key"] == "INSTAGRAM"
        for row in breakdowns
    )
    assert any(
        row["dimension"] == "TACTIC" and row["key"] == "instagram_ads"
        for row in breakdowns
    )


def test_growth_manager_scales_winner_and_stops_loser() -> None:
    product_id = _product()
    winner_plan = _run_paid_experiment(product_id, "instagram_ads")
    loser_plan = _run_paid_experiment(product_id, "tiktok_ads")
    winner = winner_plan["experiment"]
    loser = loser_plan["experiment"]

    _spend(winner["id"], 21)
    for index in range(3):
        actor = f"winner-{index}"
        _event(winner, "PAID", actor_id=actor, revenue=15)

    _spend(loser["id"], 100)
    for index in range(25):
        _event(loser, "VISIT", actor_id=f"visitor-{index}")

    winner_decision = client.post(
        f"/v1/distribution-experiments/{winner['id']}/growth-decision"
    )
    loser_decision = client.post(
        f"/v1/distribution-experiments/{loser['id']}/growth-decision"
    )

    assert winner_decision.status_code == 200
    assert loser_decision.status_code == 200
    assert winner_decision.json()["action"] == "SCALE"
    assert winner_decision.json()["recommended_budget_increment"] > 0
    assert loser_decision.json()["action"] == "STOP"
    assert loser_decision.json()["recommended_budget_increment"] == 0

    duplicate = client.post(
        f"/v1/distribution-experiments/{winner['id']}/growth-decision"
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True

    memory = client.get(f"/v1/products/{product_id}/distribution-learning")
    assert memory.status_code == 200
    actions = {entry["action"] for entry in memory.json()["entries"]}
    assert {"SCALE", "STOP"}.issubset(actions)


def test_next_portfolio_promotes_observed_winner_and_penalizes_loser() -> None:
    product_id = _product()
    winner_plan = _run_paid_experiment(product_id, "instagram_ads")
    loser_plan = _run_paid_experiment(product_id, "tiktok_ads")
    winner = winner_plan["experiment"]
    loser = loser_plan["experiment"]

    _spend(winner["id"], 21)
    for index in range(3):
        _event(winner, "PAID", actor_id=f"winner-{index}", revenue=15)
    _spend(loser["id"], 100)

    assert client.post(
        f"/v1/distribution-experiments/{winner['id']}/finish"
    ).status_code == 200
    assert client.post(
        f"/v1/distribution-experiments/{loser['id']}/finish"
    ).status_code == 200

    portfolio = client.get(
        f"/v1/products/{product_id}/distribution-portfolio?max_items=12"
    )
    assert portfolio.status_code == 200
    items = portfolio.json()["items"]
    instagram_scores = [
        item["portfolio_score"]
        for item in items
        if item["play"]["tactic_id"] == "instagram_ads"
    ]
    tiktok_scores = [
        item["portfolio_score"]
        for item in items
        if item["play"]["tactic_id"] == "tiktok_ads"
    ]
    assert instagram_scores
    assert tiktok_scores
    assert max(instagram_scores) > max(tiktok_scores)
    assert portfolio.json()["budget_remaining"] == 379


def test_analytics_rejects_events_before_experiment_is_running() -> None:
    product_id = _product()
    play = _play(product_id, "reddit_ads")
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    experiment = prepared.json()["experiment"]

    response = client.post(
        "/v1/distribution-analytics/events",
        json={
            "event_type": "VISIT",
            "experiment_id": experiment["id"],
        },
    )

    assert response.status_code == 409
    assert "RUNNING or FINISHED" in response.json()["detail"]
