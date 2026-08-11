import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.channel_service import channel_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
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
                "Budget: 200\n"
                "Goal: Acquire 100 paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    return product_id


def _generate_plays(product_id: str) -> list[dict]:
    response = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert response.status_code == 200
    return response.json()["plays"]


def _create_identity(platform: str, kind: str, action: str) -> dict:
    response = client.post(
        "/v1/distribution-identities",
        json={
            "platform": platform,
            "theme": "Relationship advice",
            "language": "English",
            "public_positioning": "Partizan Relationship Advice Scout",
            "allowed_opportunity_kinds": [kind],
            "allowed_actions": [action],
        },
    )
    assert response.status_code == 201
    return response.json()


def _activate_slot(product_id: str, identity_id: str, route: str | None = None) -> dict:
    response = client.post(
        f"/v1/products/{product_id}/campaign-slots",
        json={
            "distribution_identity_id": identity_id,
            "status": "ACTIVE",
            "attribution_route": route,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_paid_play_has_explicit_approval_and_execution_lifecycle() -> None:
    product_id = _product()
    plays = _generate_plays(product_id)
    paid = next(play for play in plays if play["tactic_id"] == "instagram_ads")

    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{paid['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert prepared.status_code == 200
    plan = prepared.json()
    action_id = plan["action"]["id"]
    experiment_id = plan["experiment"]["id"]
    assert plan["action"]["status"] == "PREPARED"
    assert plan["experiment"]["status"] == "DRAFT"
    assert "ptz_action=" in plan["experiment"]["tracking_url"]
    assert "ptz_experiment=" in plan["experiment"]["tracking_url"]

    too_early = client.post(
        f"/v1/distribution-actions/{action_id}/mark-executed",
        json={"external_reference": "campaign-123"},
    )
    assert too_early.status_code == 409

    approved = client.post(f"/v1/distribution-actions/{action_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["action"]["status"] == "APPROVED"
    assert approved.json()["experiment"]["status"] == "APPROVED"

    executed = client.post(
        f"/v1/distribution-actions/{action_id}/mark-executed",
        json={
            "external_reference": "campaign-123",
            "executed_url": "https://ads.example.com/campaign-123",
        },
    )
    assert executed.status_code == 200
    assert executed.json()["action"]["status"] == "EXECUTED"
    assert executed.json()["experiment"]["status"] == "RUNNING"
    assert executed.json()["action"]["operational_metadata"]["external_reference"] == "campaign-123"

    stored = client.get(f"/v1/distribution-experiments/{experiment_id}")
    assert stored.status_code == 200
    assert stored.json()["status"] == "RUNNING"


def test_blocked_community_play_cannot_be_prepared() -> None:
    product_id = _product()
    plays = _generate_plays(product_id)
    blocked = next(
        play
        for play in plays
        if play["tactic_id"] == "instagram_creator_comment"
        and play["status"] == "BLOCKED"
    )

    response = client.post(
        f"/v1/products/{product_id}/distribution-plays/{blocked['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )

    assert response.status_code == 409
    assert "Only READY" in response.json()["detail"]


def test_identity_backed_action_requires_active_campaign_slot() -> None:
    product_id = _product()
    identity = _create_identity("INSTAGRAM", "CREATOR_ACCOUNT", "COMMENT")
    plays = _generate_plays(product_id)
    comment = next(
        play
        for play in plays
        if play["tactic_id"] == "instagram_creator_comment"
        and play["status"] == "READY"
    )

    without_slot = client.post(
        f"/v1/products/{product_id}/distribution-plays/{comment['id']}/actions/prepare",
        json={
            "destination_url": "https://example.com/oracle",
            "target_url": "https://www.instagram.com/p/example/",
            "context_text": "Creator discusses uncertainty after a breakup.",
            "content_text": "A useful reflection is to separate facts from assumptions.",
        },
    )
    assert without_slot.status_code == 409
    assert "CampaignSlot" in without_slot.json()["detail"]

    _activate_slot(
        product_id,
        identity["id"],
        route="https://partizan.example/relationships",
    )
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{comment['id']}/actions/prepare",
        json={
            "destination_url": "https://example.com/oracle",
            "target_url": "https://www.instagram.com/p/example/",
            "context_text": "Creator discusses uncertainty after a breakup.",
            "content_text": "A useful reflection is to separate facts from assumptions.",
        },
    )
    assert prepared.status_code == 200
    plan = prepared.json()
    assert plan["action"]["campaign_slot_id"] is not None
    assert plan["experiment"]["tracking_url"].startswith(
        "https://partizan.example/relationships?"
    )


def test_comment_approval_requires_target_context_and_draft() -> None:
    product_id = _product()
    identity = _create_identity("INSTAGRAM", "CREATOR_ACCOUNT", "COMMENT")
    _activate_slot(product_id, identity["id"])
    plays = _generate_plays(product_id)
    comment = next(
        play
        for play in plays
        if play["tactic_id"] == "instagram_creator_comment"
        and play["status"] == "READY"
    )

    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{comment['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert prepared.status_code == 200
    action_id = prepared.json()["action"]["id"]

    rejected = client.post(f"/v1/distribution-actions/{action_id}/approve")
    assert rejected.status_code == 409
    assert "target URL" in rejected.json()["detail"]

    edited = client.patch(
        f"/v1/distribution-actions/{action_id}",
        json={
            "target_url": "https://www.instagram.com/p/example/",
            "context_text": "The Reel asks how to respond to mixed signals.",
            "content_text": "It can help to distinguish the signal from the story built around it.",
        },
    )
    assert edited.status_code == 200

    approved = client.post(f"/v1/distribution-actions/{action_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["action"]["status"] == "APPROVED"


def test_tiktok_organic_video_can_prepare_without_third_party_target() -> None:
    product_id = _product()
    identity = _create_identity("TIKTOK", "CONTENT_CLUSTER", "ORGANIC_VIDEO")
    _activate_slot(product_id, identity["id"])
    plays = _generate_plays(product_id)
    organic = next(
        play
        for play in plays
        if play["tactic_id"] == "tiktok_partizan_organic_video"
        and play["status"] == "READY"
    )

    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{organic['id']}/actions/prepare",
        json={
            "destination_url": "https://example.com/oracle",
            "content_text": "Hook: three questions to ask when mixed signals keep you stuck.",
        },
    )
    assert prepared.status_code == 200
    assert prepared.json()["action"]["target_url"] is None

    approved = client.post(
        f"/v1/distribution-actions/{prepared.json()['action']['id']}/approve"
    )
    assert approved.status_code == 200


def test_prepared_action_can_be_skipped_without_execution() -> None:
    product_id = _product()
    paid = next(
        play
        for play in _generate_plays(product_id)
        if play["tactic_id"] == "reddit_ads"
    )
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{paid['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    action_id = prepared.json()["action"]["id"]

    skipped = client.post(f"/v1/distribution-actions/{action_id}/skip")

    assert skipped.status_code == 200
    assert skipped.json()["action"]["status"] == "SKIPPED"
    assert skipped.json()["experiment"]["status"] == "CANCELLED"
