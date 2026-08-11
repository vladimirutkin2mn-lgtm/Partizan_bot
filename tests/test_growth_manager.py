import pytest
from fastapi.testclient import TestClient

from app.analytics_service import analytics_service
from app.channel_service import channel_service
from app.execution_service import execution_service
from app.growth_manager_service import growth_manager_service
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
    growth_play_service.reset()
    execution_service.reset()
    analytics_service.reset()
    growth_manager_service.reset()


def _build_product(budget: float = 500, max_cac: float = 5) -> tuple[str, list[dict]]:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized readings available on demand.\n"
                "Market: US\n"
                "Goal: Acquire 100 paid users\n"
                f"Budget: {budget}\n"
                f"Max CAC: {max_cac}"
            ),
            "reference_links": ["https://example.com/oracle"],
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/channels/discover").status_code == 200
    plays = client.post(f"/v1/products/{product_id}/growth-plays/generate")
    assert plays.status_code == 200
    return product_id, plays.json()["plays"]


def _launch(product_id: str, play: dict, run: bool = True) -> tuple[dict, dict]:
    approval = client.post(
        f"/v1/products/{product_id}/growth-plays/{play['id']}/approval",
        json={"status": "APPROVED"},
    )
    assert approval.status_code == 200
    prepared = client.post(
        f"/v1/products/{product_id}/growth-plays/{play['id']}/execution/prepare",
        json={"contact_email": "partner@example.com"},
    ).json()
    assert client.post(f"/v1/execution-packages/{prepared['id']}/approve").status_code == 200
    if not run:
        experiment = client.get(f"/v1/experiments/{prepared['experiment_id']}").json()
        return prepared, experiment
    launched = client.post(f"/v1/execution-packages/{prepared['id']}/run")
    assert launched.status_code == 200
    return launched.json()["package"], launched.json()["experiment"]


def _paid(experiment_id: str, actor: str, revenue: float = 9.99) -> None:
    response = client.post(
        "/v1/analytics/events",
        json={
            "event_type": "PAID",
            "experiment_id": experiment_id,
            "actor_id": actor,
            "revenue": revenue,
        },
    )
    assert response.status_code == 202


def test_growth_manager_requires_launched_experiment() -> None:
    product_id, plays = _build_product()
    _, experiment = _launch(product_id, plays[0], run=False)
    response = client.post(f"/v1/experiments/{experiment['id']}/decision")
    assert response.status_code == 409


def test_growth_manager_continues_when_signal_is_insufficient() -> None:
    product_id, plays = _build_product()
    _, experiment = _launch(product_id, plays[0])
    response = client.post(f"/v1/experiments/{experiment['id']}/decision")
    assert response.status_code == 200
    decision = response.json()
    assert decision["action"] == "CONTINUE"
    assert decision["recommended_budget_increment"] >= 0
    assert decision["next_hypothesis"]["title"] == "Collect a stronger signal"


def test_growth_manager_scales_good_cac_and_dedupes_same_snapshot() -> None:
    product_id, plays = _build_product()
    _, experiment = _launch(product_id, plays[0])
    client.post(
        f"/v1/experiments/{experiment['id']}/spend",
        json={"amount": 12},
    )
    for actor in ("u1", "u2", "u3"):
        _paid(experiment["id"], actor)

    first = client.post(f"/v1/experiments/{experiment['id']}/decision")
    assert first.status_code == 200
    decision = first.json()
    assert decision["action"] == "SCALE"
    assert decision["metrics"]["cac"] == 4.0
    assert decision["budget_remaining"] == 488.0
    assert decision["recommended_budget_increment"] <= 488.0
    assert decision["next_hypothesis"]["title"] == "Scale the winning play"

    duplicate = client.post(f"/v1/experiments/{experiment['id']}/decision")
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == decision["id"]
    assert duplicate.json()["duplicate"] is True

    history = client.get(f"/v1/experiments/{experiment['id']}/decisions")
    assert history.status_code == 200
    assert len(history.json()["decisions"]) == 1
    memory = client.get(f"/v1/products/{product_id}/learning-memory")
    assert memory.status_code == 200
    assert len(memory.json()["entries"]) == 1
    assert memory.json()["entries"][0]["action"] == "SCALE"

    _paid(experiment["id"], "u4")
    updated = client.post(f"/v1/experiments/{experiment['id']}/decision")
    assert updated.status_code == 200
    assert updated.json()["duplicate"] is False
    history = client.get(f"/v1/experiments/{experiment['id']}/decisions").json()
    assert len(history["decisions"]) == 2


def test_growth_manager_modifies_when_visits_do_not_signup() -> None:
    product_id, plays = _build_product()
    package, experiment = _launch(product_id, plays[0])
    for index in range(20):
        response = client.post(
            "/v1/analytics/events",
            json={
                "event_type": "VISIT",
                "referral_token": package["referral_token"],
                "actor_id": f"visitor-{index}",
            },
        )
        assert response.status_code == 202

    decision = client.post(f"/v1/experiments/{experiment['id']}/decision")
    assert decision.status_code == 200
    body = decision.json()
    assert body["action"] == "MODIFY"
    assert body["next_hypothesis"]["title"] == "Modify one bottleneck"
    assert "hook/CTA" in body["next_hypothesis"]["change"]


def test_growth_manager_stops_when_product_budget_is_exhausted() -> None:
    product_id, plays = _build_product(budget=20)
    _, experiment = _launch(product_id, plays[0])
    spend = client.post(
        f"/v1/experiments/{experiment['id']}/spend",
        json={"amount": 20},
    )
    assert spend.status_code == 202

    decision = client.post(f"/v1/experiments/{experiment['id']}/decision")
    assert decision.status_code == 200
    body = decision.json()
    assert body["action"] == "STOP"
    assert body["budget_remaining"] == 0.0
    assert body["recommended_budget_increment"] == 0.0
    assert "budget is exhausted" in body["rationale"][0]

    product_history = client.get(f"/v1/products/{product_id}/decisions")
    assert product_history.status_code == 200
    assert len(product_history.json()["decisions"]) == 1
