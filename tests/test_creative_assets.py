from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.creative_assets import creative_asset_service
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_service import distribution_play_service
from app.icp_service import icp_service
from app.main import app
from app.paid_campaign import paid_campaign_spec_service
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
    paid_campaign_spec_service.reset()
    creative_asset_service.reset()


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
                "Budget: 200\n"
                "Max CAC: 12\n"
                "Goal: Acquire paid users\n"
                "Constraints: No guarantees; entertainment and reflection positioning only"
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


def _prepare_paid_action(product_id: str, tactic_id: str) -> UUID:
    plays = client.get(f"/v1/products/{product_id}/distribution-plays")
    assert plays.status_code == 200
    play = next(item for item in plays.json()["plays"] if item["tactic_id"] == tactic_id)
    response = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert response.status_code == 200
    return UUID(response.json()["action"]["id"])


def _ensure_brief(action_id: UUID) -> dict:
    response = client.post(f"/v1/distribution-actions/{action_id}/creative-brief")
    assert response.status_code == 200
    return response.json()


def test_paid_creative_brief_is_normalized_and_idempotent() -> None:
    product_id = _create_product()
    action_id = _prepare_paid_action(product_id, "instagram_ads")

    first = _ensure_brief(action_id)
    second = _ensure_brief(action_id)

    assert first["id"] == second["id"]
    assert first["fingerprint"] == second["fingerprint"]
    assert len(first["fingerprint"]) == 64
    assert first["platform"] == "INSTAGRAM"
    assert first["purpose"] == "PAID_AD"
    assert first["media_type"] == "IMAGE"
    assert first["content"]["product_name"] == "Oracle"
    assert first["content"]["message_hook"]
    assert first["content"]["audience"]
    assert any("confirmed product facts" in item for item in first["constraints"])
    assert any("testimonials" in item for item in first["constraints"])


def test_meta_readiness_requires_ready_image_with_public_url() -> None:
    product_id = _create_product()
    action_id = _prepare_paid_action(product_id, "instagram_ads")
    brief = _ensure_brief(action_id)

    blocked = client.get(f"/v1/distribution-actions/{action_id}/creative-readiness")
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "BLOCKED"
    assert blocked.json()["selected_asset"] is None
    assert any("No READY IMAGE" in reason for reason in blocked.json()["reasons"])

    registered = client.post(
        "/v1/creative-assets",
        json={
            "brief_id": brief["id"],
            "source": "EXTERNAL_URL",
            "status": "READY",
            "public_url": "https://cdn.example.com/oracle/meta-creative.png",
            "mime_type": "image/png",
            "width": 1080,
            "height": 1350,
            "provenance": {"source_system": "founder_upload", "revision": 1},
        },
    )
    assert registered.status_code == 201
    asset = registered.json()
    assert asset["media_type"] == "IMAGE"
    assert asset["platform"] == "INSTAGRAM"
    assert asset["brief_fingerprint"] == brief["fingerprint"]

    ready = client.get(f"/v1/distribution-actions/{action_id}/creative-readiness")
    assert ready.status_code == 200
    assert ready.json()["status"] == "READY"
    assert ready.json()["selected_asset"]["id"] == asset["id"]

    listed = client.get(f"/v1/products/{product_id}/creative-assets")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [asset["id"]]


def test_tiktok_public_video_url_alone_is_not_provider_ready() -> None:
    product_id = _create_product()
    action_id = _prepare_paid_action(product_id, "tiktok_ads")
    brief = _ensure_brief(action_id)
    assert brief["platform"] == "TIKTOK"
    assert brief["media_type"] == "VIDEO"

    external = client.post(
        "/v1/creative-assets",
        json={
            "brief_id": brief["id"],
            "source": "EXTERNAL_URL",
            "status": "READY",
            "public_url": "https://cdn.example.com/oracle/tiktok-creative.mp4",
            "mime_type": "video/mp4",
            "duration_seconds": 12,
        },
    )
    assert external.status_code == 201

    blocked = client.get(f"/v1/distribution-actions/{action_id}/creative-readiness")
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "BLOCKED"
    assert blocked.json()["selected_asset"] is None
    assert any("provider video ID" in reason for reason in blocked.json()["reasons"])

    provider = client.post(
        "/v1/creative-assets",
        json={
            "brief_id": brief["id"],
            "source": "EXISTING_PROVIDER",
            "status": "READY",
            "provider_asset_id": "tt_video_real_123",
            "mime_type": "video/mp4",
            "duration_seconds": 12,
            "provenance": {"provider": "tiktok", "evidence": "existing_uploaded_asset"},
        },
    )
    assert provider.status_code == 201

    ready = client.get(f"/v1/distribution-actions/{action_id}/creative-readiness")
    assert ready.status_code == 200
    assert ready.json()["status"] == "READY"
    assert ready.json()["selected_asset"]["id"] == provider.json()["id"]
    assert ready.json()["selected_asset"]["provider_asset_id"] == "tt_video_real_123"


def test_secret_like_provenance_is_rejected() -> None:
    product_id = _create_product()
    action_id = _prepare_paid_action(product_id, "instagram_ads")
    brief = _ensure_brief(action_id)

    response = client.post(
        "/v1/creative-assets",
        json={
            "brief_id": brief["id"],
            "source": "EXTERNAL_URL",
            "status": "READY",
            "public_url": "https://cdn.example.com/oracle/meta.png",
            "provenance": {"generator": {"api_token": "do-not-store-this"}},
        },
    )

    assert response.status_code == 409
    assert "Secret-like provenance field" in response.json()["detail"]
    assert "do-not-store-this" not in response.text


def test_retired_asset_immediately_stops_satisfying_readiness() -> None:
    product_id = _create_product()
    action_id = _prepare_paid_action(product_id, "instagram_ads")
    brief = _ensure_brief(action_id)
    registered = client.post(
        "/v1/creative-assets",
        json={
            "brief_id": brief["id"],
            "source": "EXTERNAL_URL",
            "status": "READY",
            "public_url": "https://cdn.example.com/oracle/meta.png",
        },
    )
    assert registered.status_code == 201
    asset_id = registered.json()["id"]
    assert client.get(f"/v1/distribution-actions/{action_id}/creative-readiness").json()["status"] == "READY"

    retired = client.post(f"/v1/creative-assets/{asset_id}/retire")
    assert retired.status_code == 200
    assert retired.json()["status"] == "RETIRED"

    blocked = client.get(f"/v1/distribution-actions/{action_id}/creative-readiness")
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "BLOCKED"
    assert blocked.json()["selected_asset"] is None
