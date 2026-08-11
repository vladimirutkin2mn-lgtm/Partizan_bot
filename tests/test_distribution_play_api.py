import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.channel_service import channel_service
from app.distribution_control_plane_service import distribution_control_plane_service
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
                "Budget: 200\n"
                "Goal: Acquire 100 paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    confirmed = client.post(f"/v1/products/{product_id}/confirm")
    assert confirmed.status_code == 200
    return product_id


def test_distribution_play_generation_requires_audience_intelligence() -> None:
    product_id = _confirmed_product()

    response = client.post(f"/v1/products/{product_id}/distribution-plays/generate")

    assert response.status_code == 409


def test_distribution_play_api_uses_only_mvp_platforms_and_concrete_opportunities() -> None:
    product_id = _confirmed_product()
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    distribution = client.post(f"/v1/products/{product_id}/distribution/discover")
    assert distribution.status_code == 200

    response = client.post(f"/v1/products/{product_id}/distribution-plays/generate")

    assert response.status_code == 200
    body = response.json()
    opportunity_ids = {item["id"] for item in distribution.json()["opportunities"]}
    assert body["play_count"] == len(body["plays"])
    assert body["ready_count"] > 0
    assert body["blocked_count"] > 0
    assert {play["platform"] for play in body["plays"]} == {
        "TELEGRAM",
        "INSTAGRAM",
        "REDDIT",
        "TIKTOK",
    }
    assert all(play["opportunity_id"] in opportunity_ids for play in body["plays"])
    assert all(
        play["status"] == "READY"
        for play in body["plays"]
        if play["action_type"] == "PAID_CAMPAIGN"
    )


def test_distribution_play_api_keeps_legacy_growth_play_path_available() -> None:
    product_id = _confirmed_product()
    client.post(f"/v1/products/{product_id}/icps/generate")
    client.post(f"/v1/products/{product_id}/distribution/discover")
    new_path = client.post(f"/v1/products/{product_id}/distribution-plays/generate")

    legacy_channels = client.post(f"/v1/products/{product_id}/channels/discover")
    legacy_plays = client.post(f"/v1/products/{product_id}/growth-plays/generate")

    assert new_path.status_code == 200
    assert legacy_channels.status_code == 200
    assert legacy_plays.status_code == 200
    stored = client.get(f"/v1/products/{product_id}/distribution-plays")
    assert stored.status_code == 200
    assert stored.json() == new_path.json()
