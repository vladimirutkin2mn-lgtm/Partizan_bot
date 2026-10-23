from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.action_drafting import (
    ActionTargetSelector,
    DistributionActionDraftingService,
    SelectedActionTarget,
)
from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.distribution_types import (
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
)
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
    target = {
        "url": target_url,
        "title": target_title,
        "snippet": target_snippet,
    }
    if platform == "REDDIT":
        now = datetime.now(UTC)
        target.update(
            {
                "freshness_status": "FRESH",
                "published_at": now.isoformat(),
                "checked_at": now.isoformat(),
            }
        )
    enrichment["action_targets"] = [target]
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


def _allow_reddit_comments(opportunity_id: str) -> None:
    policy = client.put(
        f"/v1/distribution-opportunities/{opportunity_id}/community-policy",
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
    _allow_reddit_comments(reddit["id"])
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


def test_reddit_comment_rejects_stale_target_and_raw_evidence_fallback() -> None:
    product_id = _product()
    reddit = _replace_opportunity_target(
        product_id,
        "REDDIT",
        "https://www.reddit.com/r/relationships/comments/stale1/old_thread/",
        "Old relationship thread",
        "An old discussion that must not be selected for a new reply.",
    )
    opportunity = audience_intelligence_service.find_opportunity(reddit["id"])
    metadata = dict(opportunity.metadata)
    enrichment = dict(metadata["enrichment"])
    enrichment["action_targets"][0]["freshness_status"] = "STALE"
    enrichment["action_targets"][0]["published_at"] = (
        datetime.now(UTC) - timedelta(days=10)
    ).isoformat()
    metadata["enrichment"] = enrichment
    evidence = [
        *opportunity.evidence,
        {
            "url": "https://www.reddit.com/r/relationships/comments/raw1/raw_thread/",
            "title": "Unverified evidence thread",
            "snippet": "No verified publication time is available.",
        },
    ]
    audience_intelligence_service.update_opportunity(
        opportunity.model_copy(update={"metadata": metadata, "evidence": evidence})
    )
    identity = _identity("REDDIT", "SUBREDDIT", ["COMMENT"])
    _slot(product_id, identity["id"])
    _allow_reddit_comments(reddit["id"])
    comment = next(
        play
        for play in _plays(product_id)
        if play["tactic_id"] == "reddit_comment"
        and play["opportunity_id"] == reddit["id"]
        and play["status"] == "READY"
    )

    response = _auto_prepare(product_id, comment["id"])

    assert response.status_code == 409
    assert "ActionTarget" in response.json()["detail"]


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
    opportunity = audience_intelligence_service.find_opportunity(telegram["id"])
    metadata = dict(opportunity.metadata)
    metadata.update(
        {
            "native_research_status": "VERIFIED",
            "action_target_specific": True,
            "action_target_url": "https://t.me/relationship_daily/321",
            "surface_capabilities": {
                "comment": "AVAILABLE",
                "reply": "UNKNOWN",
                "standalone_post": "UNAVAILABLE",
                "publisher_permission_verified": False,
            },
            "recent_context": [
                {
                    "url": "https://t.me/relationship_daily/321",
                    "text": "A public post asks readers how they distinguish facts from assumptions.",
                }
            ],
        }
    )
    audience_intelligence_service.update_opportunity(
        opportunity.model_copy(update={"metadata": metadata})
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


def _telegram_selector_opportunity(
    *,
    target_url: str | None,
    recent_context: list[dict],
    verified: bool = True,
):
    return SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        title="Real native community",
        rationale="Evidence-backed community",
        url="https://t.me/community",
        evidence=[
            {
                "url": "https://t.me/community/111",
                "snippet": "Older search result must not become the action target",
            }
        ],
        metadata={
            "native_research_status": "VERIFIED" if verified else "NOT_CHECKED",
            "action_target_specific": bool(target_url),
            "action_target_mode": "CHANNEL_COMMENT",
            "action_target_url": target_url,
            "enrichment": {
                "action_targets": [
                    {
                        "url": "https://t.me/community/111",
                        "snippet": "Older post returned by search",
                    }
                ]
            },
            "recent_context": recent_context,
        },
    )


def test_telegram_drafting_never_uses_enrichment_when_latest_is_blocked() -> None:
    opportunity = _telegram_selector_opportunity(
        target_url=None,
        recent_context=[
            {"message_id": 147, "url": "https://t.me/community/147", "text": "Latest"},
            {"message_id": 111, "url": "https://t.me/community/111", "text": "Older"},
        ],
    )
    play = SimpleNamespace(action_type=DistributionActionType.COMMENT)
    with pytest.raises(ValueError, match="latest"):
        ActionTargetSelector().select(play, opportunity)


def test_telegram_drafting_rejects_stale_native_target_even_if_enrichment_has_it() -> None:
    opportunity = _telegram_selector_opportunity(
        target_url="https://t.me/community/111",
        recent_context=[
            {"message_id": 147, "url": "https://t.me/community/147", "text": "Latest"},
            {"message_id": 111, "url": "https://t.me/community/111", "text": "Older"},
        ],
    )
    play = SimpleNamespace(action_type=DistributionActionType.COMMENT)
    with pytest.raises(ValueError, match="not the latest"):
        ActionTargetSelector().select(play, opportunity)


def test_telegram_drafting_uses_verified_latest_with_unknown_date() -> None:
    opportunity = _telegram_selector_opportunity(
        target_url="https://t.me/community/147",
        recent_context=[
            {"message_id": 147, "url": "https://t.me/community/147", "text": "Caption: встречаемся в субботу, обсудим расписание."}
        ],
    )
    play = SimpleNamespace(action_type=DistributionActionType.COMMENT)
    target = ActionTargetSelector().select(play, opportunity)
    assert target.url == "https://t.me/community/147"
    assert target.source == "telegram_native.latest_message"


def test_telegram_drafting_requires_native_observation_and_correct_action_mode() -> None:
    opportunity = _telegram_selector_opportunity(
        target_url="https://t.me/community/147",
        recent_context=[
            {"message_id": 147, "url": "https://t.me/community/147", "text": "Caption"}
        ],
        verified=False,
    )
    play = SimpleNamespace(action_type=DistributionActionType.COMMENT)
    with pytest.raises(ValueError, match="verified native"):
        ActionTargetSelector().select(play, opportunity)

    opportunity.metadata["native_research_status"] = "VERIFIED"
    opportunity.metadata["action_target_mode"] = "GROUP_REPLY"
    with pytest.raises(ValueError, match="mode differs"):
        ActionTargetSelector().select(play, opportunity)


def _processed_telegram_target_fixture(monkeypatch, *, status, action_type=None):
    product = SimpleNamespace(id=uuid4())
    identity_id = uuid4()
    action_id = uuid4()
    play = SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        action_type=action_type or DistributionActionType.COMMENT,
        selected_identity_id=identity_id,
    )
    experiment = SimpleNamespace(action_id=action_id)
    saved = SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        action_type=play.action_type,
        distribution_identity_id=identity_id,
        target_url="https://t.me/community/147",
        status=status,
    )
    monkeypatch.setattr(
        distribution_execution_service, "list_experiments", lambda product_id: [experiment]
    )
    monkeypatch.setattr(
        distribution_execution_service, "get_action", lambda checked_id: saved
    )
    return product, play, saved


@pytest.mark.parametrize(
    "status",
    [
        DistributionActionStatus.PREPARED,
        DistributionActionStatus.APPROVED,
        DistributionActionStatus.EXECUTED,
        DistributionActionStatus.FAILED,
    ],
)
def test_telegram_target_already_attempted_is_not_drafted_again(
    monkeypatch, status
) -> None:
    product, play, _ = _processed_telegram_target_fixture(monkeypatch, status=status)
    target = SelectedActionTarget("https://t.me/community/147", "context", "native")
    with pytest.raises(ValueError, match="already has a prepared"):
        DistributionActionDraftingService._require_unprocessed_telegram_target(
            product, play, target
        )


def test_telegram_repeat_guard_is_scoped_to_identity_action_kind_and_message(
    monkeypatch,
) -> None:
    product, play, saved = _processed_telegram_target_fixture(
        monkeypatch, status=DistributionActionStatus.EXECUTED
    )
    service = DistributionActionDraftingService
    target = SelectedActionTarget("https://t.me/community/148", "new context", "native")
    service._require_unprocessed_telegram_target(product, play, target)
    saved.target_url = "https://t.me/community/147"
    play.selected_identity_id = uuid4()
    service._require_unprocessed_telegram_target(
        product, play, SelectedActionTarget("https://t.me/community/147", "x", "native")
    )
    play.selected_identity_id = saved.distribution_identity_id
    play.action_type = DistributionActionType.REPLY
    service._require_unprocessed_telegram_target(
        product, play, SelectedActionTarget("https://t.me/community/147", "x", "native")
    )
    play.action_type = DistributionActionType.STANDALONE_POST
    service._require_unprocessed_telegram_target(
        product, play, SelectedActionTarget("https://t.me/community", "x", "native")
    )


def test_skipped_telegram_target_can_be_drafted_again(monkeypatch) -> None:
    product, play, _ = _processed_telegram_target_fixture(
        monkeypatch, status=DistributionActionStatus.SKIPPED
    )
    DistributionActionDraftingService._require_unprocessed_telegram_target(
        product,
        play,
        SelectedActionTarget("https://t.me/community/147", "context", "native"),
    )


def test_telegram_latest_empty_post_does_not_trigger_generic_promotion() -> None:
    opportunity = _telegram_selector_opportunity(
        target_url="https://t.me/community/147",
        recent_context=[
            {"message_id": 147, "url": "https://t.me/community/147", "text": "   "},
            {
                "message_id": 146,
                "url": "https://t.me/community/146",
                "text": "Older detailed thread might be useful but cannot replace latest",
            },
        ],
    )
    play = SimpleNamespace(action_type=DistributionActionType.COMMENT)
    with pytest.raises(ValueError, match="no readable context"):
        ActionTargetSelector().select(play, opportunity)
