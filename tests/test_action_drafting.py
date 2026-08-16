import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
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


def _opportunity(product_id: str, platform: str, kind: str | None = None) -> dict:
    response = client.get(f"/v1/products/{product_id}/distribution")
    assert response.status_code == 200
    return next(
        item
        for item in response.json()["opportunities"]
        if item["platform"] == platform and (kind is None or item["kind"] == kind)
    )


def _replace_opportunity_target(
    product_id: str,
    platform: str,
    target_url: str,
    target_title: str,
    target_snippet: str,
    *,
    kind: str | None = None,
) -> dict:
    current = _opportunity(product_id, platform, kind)
    opportunity = audience_intelligence_service.find_opportunity(current["id"])
    metadata = dict(opportunity.metadata)
    enrichment = dict(metadata.get("enrichment", {}))
    enrichment["action_targets"] = [
        {
            "url": target_url,
            "title": target_title,
            "snippet": target_snippet,
        }
    ]
    metadata["enrichment"] = enrichment
    updated = opportunity.model_copy(update={"metadata": metadata})
    audience_intelligence_service.update_opportunity(updated)
    return updated.model_dump(mode="json")


def _identity(platform: str, kind: str, actions: list[str]) -> dict:
    response = client.post(
        "/v1/distribution-identities",
        json={
            "platform": platform,
            "theme": "Relationship advice",
            "language": "English",
            "public_positioning": "Partizan-operated relationship tools account",
            "allowed_opportunity_kinds": [kind],
            "allowed_actions": actions,
        },
    )
    assert response.status_code == 201
    return response.json()


def _slot(product_id: str, identity_id: str) -> dict:
    response = client.post(
        f"/v1/products/{product_id}/campaign-slots",
        json={
            "distribution_identity_id": identity_id,
            "status": "ACTIVE",
            "attribution_route": "https://partizan.example/relationships",
        },
    )
    assert response.status_code == 201
    return response.json()


def _plays(product_id: str) -> list[dict]:
    response = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert response.status_code == 200
    return response.json()["plays"]


def _auto_prepare(product_id: str, play_id: str):
    return client.post(
        f"/v1/products/{product_id}/distribution-plays/{play_id}/actions/auto-prepare",
        json={"destination_url": "https://example.com/oracle"},
    )


def test_instagram_comment_selects_enriched_reel_and_creates_prepared_draft() -> None:
    product_id = _product()
    instagram = _replace_opportunity_target(
        product_id,
        "INSTAGRAM",
        "https://www.instagram.com/reel/ABC123/",
        "Mixed signals after a breakup",
        "The creator discusses how people interpret mixed signals after a breakup.",
    )
    identity = _identity("INSTAGRAM", "CREATOR_ACCOUNT", ["COMMENT"])
    _slot(product_id, identity["id"])
    comment = next(
        play
        for play in _plays(product_id)
        if play["tactic_id"] == "instagram_creator_comment"
        and play["opportunity_id"] == instagram["id"]
        and play["status"] == "READY"
    )

    response = _auto_prepare(product_id, comment["id"])

    assert response.status_code == 200
    plan = response.json()
    assert plan["action"]["status"] == "PREPARED"
    assert plan["experiment"]["status"] == "DRAFT"
    assert plan["action"]["target_url"] == "https://www.instagram.com/reel/ABC123/"
    assert "mixed signals" in plan["action"]["content_payload"]["context_text"].lower()
    assert plan["action"]["content_text"]


def test_comment_auto_prepare_rejects_missing_concrete_target() -> None:
    product_id = _product()
    identity = _identity("INSTAGRAM", "CREATOR_ACCOUNT", ["COMMENT"])
    _slot(product_id, identity["id"])
    comment = next(
        play
        for play in _plays(product_id)
        if play["tactic_id"] == "instagram_creator_comment"
        and play["status"] == "READY"
    )

    response = _auto_prepare(product_id, comment["id"])

    assert response.status_code == 409
    assert "ActionTarget" in response.json()["detail"]


def test_reddit_comment_uses_applied_policy_and_includes_required_disclosure() -> None:
    product_id = _product()
    reddit = _replace_opportunity_target(
        product_id,
        "REDDIT",
        "https://www.reddit.com/r/relationships/comments/abc123/mixed_signals/",
        "How should I read these mixed signals?",
        "The thread asks for perspectives on uncertainty in a relationship.",
    )
    identity = _identity("REDDIT", "SUBREDDIT", ["COMMENT"])
    _slot(product_id, identity["id"])
    policy = client.put(
        f"/v1/distribution-opportunities/{reddit['id']}/community-policy",
        json={
            "commercial_participation_allowed": True,
            "comments_allowed": True,
            "standalone_posts_allowed": False,
            "links_allowed": False,
            "product_mentions_allowed": False,
            "disclosure_required": True,
            "confidence": 95,
            "evidence": [{"source": "reviewed subreddit rules"}],
        },
    )
    assert policy.status_code == 200
    comment = next(
        play
        for play in _plays(product_id)
        if play["tactic_id"] == "reddit_comment"
        and play["opportunity_id"] == reddit["id"]
        and play["status"] == "READY"
    )

    response = _auto_prepare(product_id, comment["id"])

    assert response.status_code == 200
    action = response.json()["action"]
    assert "/comments/abc123/" in action["target_url"]
    assert action["content_text"].startswith("Disclosure:")
    assert "http" not in action["content_text"].lower()


def test_tiktok_organic_auto_prepare_requires_no_third_party_target() -> None:
    product_id = _product()
    identity = _identity("TIKTOK", "CONTENT_CLUSTER", ["ORGANIC_VIDEO"])
    _slot(product_id, identity["id"])
    organic = next(
        play
        for play in _plays(product_id)
        if play["tactic_id"] == "tiktok_partizan_organic_video"
        and play["status"] == "READY"
    )

    response = _auto_prepare(product_id, organic["id"])

    assert response.status_code == 200
    action = response.json()["action"]
    assert action["target_url"] is None
    assert action["content_text"].startswith("Hook:")


def test_telegram_comment_accepts_concrete_public_post_target() -> None:
    product_id = _product()
    telegram = _replace_opportunity_target(
        product_id,
        "TELEGRAM",
        "https://t.me/relationship_daily/321",
        "Daily relationship prompt",
        "A public post asks readers how they distinguish facts from assumptions.",
        kind="CHANNEL",
    )
    identity = _identity("TELEGRAM", "CHANNEL", ["COMMENT"])
    _slot(product_id, identity["id"])
    comment = next(
        play
        for play in _plays(product_id)
        if play["tactic_id"] == "telegram_channel_comment"
        and play["opportunity_id"] == telegram["id"]
        and play["status"] == "READY"
    )

    response = _auto_prepare(product_id, comment["id"])

    assert response.status_code == 200
    action = response.json()["action"]
    assert action["target_url"] == "https://t.me/relationship_daily/321"
    assert "public post" in action["content_payload"]["context_text"].lower()
