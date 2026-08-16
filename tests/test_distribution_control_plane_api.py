import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_control_plane_service import distribution_control_plane_service
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


def _create_product(name: str = "Oracle") -> str:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                f"Product: {name}\n"
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
    return product_id


def _discover(product_id: str) -> dict:
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    response = client.post(f"/v1/products/{product_id}/distribution/discover")
    assert response.status_code == 200
    return response.json()


def _create_identity(platform: str, kind: str, action: str, theme: str) -> dict:
    response = client.post(
        "/v1/distribution-identities",
        json={
            "platform": platform,
            "theme": theme,
            "language": "English",
            "public_positioning": f"Partizan {theme} Scout",
            "allowed_opportunity_kinds": [kind],
            "allowed_actions": [action],
        },
    )
    assert response.status_code == 201
    return response.json()


def test_instagram_identity_turns_blocked_comment_into_ready_play() -> None:
    product_id = _create_product()
    _discover(product_id)

    before = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert before.status_code == 200
    before_comments = [
        play
        for play in before.json()["plays"]
        if play["tactic_id"] == "instagram_creator_comment"
    ]
    assert before_comments
    assert all(play["status"] == "BLOCKED" for play in before_comments)

    identity = _create_identity(
        "INSTAGRAM",
        "CREATOR_ACCOUNT",
        "COMMENT",
        "Relationship advice",
    )
    after = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert after.status_code == 200
    after_comments = [
        play
        for play in after.json()["plays"]
        if play["tactic_id"] == "instagram_creator_comment"
    ]

    assert after_comments
    assert all(play["status"] == "READY" for play in after_comments)
    assert all(play["selected_identity_id"] == identity["id"] for play in after_comments)


def test_pausing_identity_blocks_future_community_plans() -> None:
    product_id = _create_product()
    _discover(product_id)
    identity = _create_identity(
        "TIKTOK",
        "CONTENT_CLUSTER",
        "ORGANIC_VIDEO",
        "Relationship advice",
    )

    ready = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    organic = [
        play
        for play in ready.json()["plays"]
        if play["tactic_id"] == "tiktok_partizan_organic_video"
    ]
    assert organic
    assert all(play["status"] == "READY" for play in organic)

    paused = client.patch(
        f"/v1/distribution-identities/{identity['id']}/status",
        json={"status": "PAUSED"},
    )
    assert paused.status_code == 200

    blocked = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    organic = [
        play
        for play in blocked.json()["plays"]
        if play["tactic_id"] == "tiktok_partizan_organic_video"
    ]
    assert organic
    assert all(play["status"] == "BLOCKED" for play in organic)


def test_reddit_policy_allows_comment_but_keeps_direct_link_post_blocked() -> None:
    product_id = _create_product()
    distribution = _discover(product_id)
    reddit = next(
        item for item in distribution["opportunities"] if item["platform"] == "REDDIT"
    )
    identity = _create_identity(
        "REDDIT",
        "SUBREDDIT",
        "COMMENT",
        "Relationship advice",
    )

    policy = client.put(
        f"/v1/distribution-opportunities/{reddit['id']}/community-policy",
        json={
            "commercial_participation_allowed": True,
            "comments_allowed": True,
            "standalone_posts_allowed": True,
            "links_allowed": False,
            "product_mentions_allowed": True,
            "disclosure_required": True,
            "confidence": 90,
            "evidence": [{"source": "subreddit rules"}],
        },
    )
    assert policy.status_code == 200

    generated = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert generated.status_code == 200
    plays = [
        play
        for play in generated.json()["plays"]
        if play["opportunity_id"] == reddit["id"]
    ]
    comment = next(play for play in plays if play["tactic_id"] == "reddit_comment")
    value_post = next(play for play in plays if play["tactic_id"] == "reddit_value_post")

    assert comment["status"] == "READY"
    assert comment["selected_identity_id"] == identity["id"]
    assert value_post["status"] == "BLOCKED"
    assert any("direct links" in blocker for blocker in value_post["blockers"])


def test_active_campaign_slot_cannot_be_shared_between_clients() -> None:
    first_product = _create_product("Oracle A")
    second_product = _create_product("Oracle B")
    identity = _create_identity(
        "INSTAGRAM",
        "CREATOR_ACCOUNT",
        "COMMENT",
        "Relationship advice",
    )

    first = client.post(
        f"/v1/products/{first_product}/campaign-slots",
        json={
            "distribution_identity_id": identity["id"],
            "status": "ACTIVE",
        },
    )
    assert first.status_code == 201

    second = client.post(
        f"/v1/products/{second_product}/campaign-slots",
        json={
            "distribution_identity_id": identity["id"],
            "status": "ACTIVE",
        },
    )
    assert second.status_code == 409
    assert "ACTIVE campaign slot" in second.json()["detail"]


def test_identity_eligibility_rejects_cross_platform_action() -> None:
    response = client.post(
        "/v1/distribution-identities",
        json={
            "platform": "INSTAGRAM",
            "theme": "Relationships",
            "public_positioning": "Partizan Relationships Scout",
            "allowed_opportunity_kinds": ["CREATOR_ACCOUNT"],
            "allowed_actions": ["ORGANIC_VIDEO"],
        },
    )

    assert response.status_code == 422
