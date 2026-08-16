from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.creative_assets import creative_asset_service
from app.creative_generation import (
    CreativeGenerationOutcome,
    CreativeGenerationService,
    CreativeGeneratorResult,
    DeterministicMockCreativeGenerator,
)
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.icp_service import icp_service
from app.main import app
from app.paid_campaign import paid_campaign_spec_service
from app.product_intake import product_intake_service

client = TestClient(app)


class FailedGenerator:
    def generate(self, brief):
        return CreativeGeneratorResult(
            outcome=CreativeGenerationOutcome.FAILED,
            message="Generator returned a sanitized failure.",
            provenance={"generator": "failed_test"},
        )


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    audience_intelligence_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    paid_campaign_spec_service.reset()
    creative_asset_service.reset()


def _paid_action(tactic_id: str) -> UUID:
    created = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized readings available on demand.\n"
                "Market: US\nLanguage: English\nBudget: 200\nMax CAC: 5\n"
                "Goal: Acquire paid users"
            )
        },
    )
    assert created.status_code == 201
    product_id = created.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert plays.status_code == 200
    play = next(item for item in plays.json()["plays"] if item["tactic_id"] == tactic_id)
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert prepared.status_code == 200
    return UUID(prepared.json()["action"]["id"])


def test_production_default_generate_endpoint_fails_closed_when_generator_unavailable() -> None:
    action_id = _paid_action("instagram_ads")

    response = client.post(f"/v1/distribution-actions/{action_id}/creative-generate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["outcome"] == "UNAVAILABLE"
    assert payload["asset"] is None
    assert payload["readiness"]["status"] == "BLOCKED"
    assert "No creative generator/upload provider is configured" in payload["message"]


def test_mock_image_generator_creates_provider_ready_asset_and_reuses_it() -> None:
    action_id = _paid_action("instagram_ads")
    service = CreativeGenerationService(DeterministicMockCreativeGenerator())

    first = service.ensure_ready(action_id)
    second = service.ensure_ready(action_id)

    assert first.outcome == CreativeGenerationOutcome.READY
    assert first.readiness.status == "READY"
    assert first.asset is not None
    assert first.asset.media_type == "IMAGE"
    assert first.asset.public_url is not None
    assert second.asset is not None
    assert second.asset.id == first.asset.id
    product_assets = creative_asset_service.list_assets(first.brief.product_id)
    assert [asset.id for asset in product_assets] == [first.asset.id]


def test_mock_tiktok_generator_produces_explicit_provider_video_reference() -> None:
    action_id = _paid_action("tiktok_ads")
    service = CreativeGenerationService(DeterministicMockCreativeGenerator())

    result = service.ensure_ready(action_id)

    assert result.outcome == CreativeGenerationOutcome.READY
    assert result.asset is not None
    assert result.asset.media_type == "VIDEO"
    assert result.asset.provider_asset_id is not None
    assert result.asset.provider_asset_id.startswith("mock_tiktok_video_")
    assert result.readiness.status == "READY"
    assert result.readiness.selected_asset is not None
    assert result.readiness.selected_asset.id == result.asset.id


def test_failed_generator_persists_failure_but_never_marks_readiness_ready() -> None:
    action_id = _paid_action("instagram_ads")
    service = CreativeGenerationService(FailedGenerator())

    result = service.ensure_ready(action_id)

    assert result.outcome == CreativeGenerationOutcome.FAILED
    assert result.asset is not None
    assert result.asset.status == "FAILED"
    assert result.asset.failure_reason == "Generator returned a sanitized failure."
    assert result.readiness.status == "BLOCKED"
    assert creative_asset_service.readiness(action_id).status == "BLOCKED"
