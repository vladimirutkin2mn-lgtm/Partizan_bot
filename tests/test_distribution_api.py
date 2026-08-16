import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.icp_service import icp_service
from app.main import app
from app.product_intake import product_intake_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    audience_intelligence_service.reset()


def _confirmed_product() -> str:
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
                "Goal: Acquire 100 paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    confirmed = client.post(f"/v1/products/{product_id}/confirm")
    assert confirmed.status_code == 200
    return product_id


def test_distribution_discovery_requires_icps() -> None:
    product_id = _confirmed_product()

    response = client.post(f"/v1/products/{product_id}/distribution/discover")

    assert response.status_code == 409


def test_distribution_discovery_returns_only_mvp_platforms() -> None:
    product_id = _confirmed_product()
    generated = client.post(f"/v1/products/{product_id}/icps/generate")
    assert generated.status_code == 200

    response = client.post(f"/v1/products/{product_id}/distribution/discover")

    assert response.status_code == 200
    body = response.json()
    assert body["top_icp_count"] == 3
    assert body["opportunity_count"] > 0
    assert {item["platform"] for item in body["opportunities"]} == {
        "TELEGRAM",
        "INSTAGRAM",
        "REDDIT",
        "TIKTOK",
    }
    assert {item["kind"] for item in body["opportunities"]} == {
        "CHANNEL",
        "GROUP",
        "CREATOR_ACCOUNT",
        "SUBREDDIT",
        "CONTENT_CLUSTER",
    }


def test_distribution_discovery_can_be_retrieved() -> None:
    product_id = _confirmed_product()
    client.post(f"/v1/products/{product_id}/icps/generate")

    distribution = client.post(f"/v1/products/{product_id}/distribution/discover")
    stored = client.get(f"/v1/products/{product_id}/distribution")

    assert distribution.status_code == 200
    assert stored.status_code == 200
    assert stored.json() == distribution.json()
