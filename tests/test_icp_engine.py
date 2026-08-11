import pytest
from fastapi.testclient import TestClient

from app.icp_agent import ICPCandidate, ICPDimensionScores, ICPEngine
from app.icp_service import icp_service
from app.main import app
from app.product_intake import product_intake_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()


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
                "Goal: Acquire 100 paid users"
            )
        },
    )
    assert response.status_code == 201
    product_id = response.json()["product"]["id"]
    assert response.json()["next_action"] == "confirm"
    return product_id


def _candidate(title: str, description: str, score: int = 8) -> ICPCandidate:
    return ICPCandidate(
        title=title,
        description=description,
        pain="Relationship uncertainty",
        desired_outcome="Get clarity quickly",
        trigger="A confusing interaction with a partner",
        willingness_to_pay_hypothesis="Higher when uncertainty feels urgent",
        alternatives=["friends", "generic AI"],
        message_hook="Get a personalized perspective now",
        dimensions=ICPDimensionScores(
            pain_intensity=score,
            purchase_intent=score,
            willingness_to_pay=score,
            ease_of_targeting=score,
            market_size=score,
            competitive_headroom=score,
            speed_of_validation=score,
        ),
        rationale=["Test hypothesis"],
    )


def test_icp_generation_requires_confirmed_product() -> None:
    product_id = _create_product()
    response = client.post(f"/v1/products/{product_id}/icps/generate")
    assert response.status_code == 409


def test_icp_generation_returns_ranked_explainable_segments() -> None:
    product_id = _create_product()
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200

    response = client.post(f"/v1/products/{product_id}/icps/generate")
    assert response.status_code == 200
    body = response.json()
    assert body["generated_count"] >= 10
    assert body["ranked_count"] >= 10
    assert len(body["icps"]) >= 10
    assert [item["rank"] for item in body["icps"]] == list(
        range(1, len(body["icps"]) + 1)
    )
    scores = [item["score"] for item in body["icps"]]
    assert scores == sorted(scores, reverse=True)
    assert all(item["score_explanation"] for item in body["icps"])
    assert all(item["message_hook"] for item in body["icps"])


def test_icp_generation_can_be_retrieved() -> None:
    product_id = _create_product()
    client.post(f"/v1/products/{product_id}/confirm")
    generated = client.post(f"/v1/products/{product_id}/icps/generate").json()
    stored = client.get(f"/v1/products/{product_id}/icps")
    assert stored.status_code == 200
    assert stored.json() == generated


def test_score_is_deterministic_and_bounded() -> None:
    engine = ICPEngine(provider=None)
    perfect = ICPDimensionScores(
        pain_intensity=10,
        purchase_intent=10,
        willingness_to_pay=10,
        ease_of_targeting=10,
        market_size=10,
        competitive_headroom=10,
        speed_of_validation=10,
    )
    weak = ICPDimensionScores(
        pain_intensity=1,
        purchase_intent=1,
        willingness_to_pay=1,
        ease_of_targeting=1,
        market_size=1,
        competitive_headroom=1,
        speed_of_validation=1,
    )
    assert engine.calculate_score(perfect) == 100.0
    assert engine.calculate_score(weak) == 10.0


def test_duplicate_clustering_marks_near_identical_segments() -> None:
    engine = ICPEngine(provider=None)
    result = engine.rank(
        [
            _candidate(
                "Urgent breakup uncertainty",
                "People urgently seeking relationship clarity after a breakup event.",
                9,
            ),
            _candidate(
                "Urgent breakup clarity seekers",
                "People urgently seeking relationship clarity after a breakup event.",
                8,
            ),
            _candidate(
                "Casual astrology explorers",
                "People casually exploring relationship and astrology entertainment.",
                6,
            ),
        ]
    )
    assert result.duplicate_clusters
    duplicate_titles = {
        title
        for titles in result.duplicate_clusters.values()
        for title in titles
    }
    assert "Urgent breakup clarity seekers" in duplicate_titles
