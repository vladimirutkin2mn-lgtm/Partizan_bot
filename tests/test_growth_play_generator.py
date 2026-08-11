import pytest
from fastapi.testclient import TestClient

from app.channel_service import channel_service
from app.growth_play_agent import GrowthPlayGenerator, PlayScoreDimensions
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


def _build_discovery(budget: float = 500) -> tuple[str, dict]:
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
                "Max CAC: 5"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    channels = client.post(f"/v1/products/{product_id}/channels/discover")
    assert channels.status_code == 200
    return product_id, channels.json()


def test_growth_play_generation_requires_discovery() -> None:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI relationship reading product.\n"
                "Problem: Relationship uncertainty.\n"
                "Value proposition: Personalized readings.\n"
                "Market: US\n"
                "Goal: Acquire paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    client.post(f"/v1/products/{product_id}/confirm")
    result = client.post(f"/v1/products/{product_id}/growth-plays/generate")
    assert result.status_code == 409


def test_growth_play_generation_returns_ranked_executable_plays() -> None:
    product_id, channels = _build_discovery()
    channel_ids = {item["id"] for item in channels["opportunities"]}

    response = client.post(f"/v1/products/{product_id}/growth-plays/generate")
    assert response.status_code == 200
    body = response.json()
    assert body["play_count"] >= 20
    assert len(body["plays"]) >= 20
    assert [play["rank"] for play in body["plays"]] == list(
        range(1, len(body["plays"]) + 1)
    )
    scores = [play["priority_score"] for play in body["plays"]]
    assert scores == sorted(scores, reverse=True)
    assert all(play["status"] == "PROPOSED" for play in body["plays"])
    assert all(len(play["execution_steps"]) >= 3 for play in body["plays"])
    assert all(play["channel_id"] in channel_ids for play in body["plays"])
    assert all(play["kill_criteria"] and play["scale_criteria"] for play in body["plays"])
    assert len({play["source_type"] for play in body["plays"]}) == 3


def test_growth_play_approval_updates_stored_result() -> None:
    product_id, _ = _build_discovery()
    generated = client.post(f"/v1/products/{product_id}/growth-plays/generate").json()
    play = generated["plays"][0]

    approval = client.post(
        f"/v1/products/{product_id}/growth-plays/{play['id']}/approval",
        json={"status": "APPROVED"},
    )
    assert approval.status_code == 200
    assert approval.json()["status"] == "APPROVED"

    stored = client.get(f"/v1/products/{product_id}/growth-plays")
    assert stored.status_code == 200
    updated = next(item for item in stored.json()["plays"] if item["id"] == play["id"])
    assert updated["status"] == "APPROVED"


def test_priority_is_deterministic_and_bounded() -> None:
    generator = GrowthPlayGenerator(provider=None)
    perfect = PlayScoreDimensions(
        expected_impact=10,
        confidence=10,
        cost_efficiency=10,
        speed_to_signal=10,
    )
    weak = PlayScoreDimensions(
        expected_impact=1,
        confidence=1,
        cost_efficiency=1,
        speed_to_signal=1,
    )
    assert generator.calculate_priority(perfect) == 100.0
    assert generator.calculate_priority(weak) == 10.0


def test_fallback_play_budget_never_exceeds_product_budget() -> None:
    product_id, _ = _build_discovery(budget=100)
    body = client.post(f"/v1/products/{product_id}/growth-plays/generate").json()
    assert all(play["estimated_cost_max"] <= 100 for play in body["plays"])
