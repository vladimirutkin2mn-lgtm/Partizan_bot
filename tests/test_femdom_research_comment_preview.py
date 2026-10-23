"""Research previews for adjacent Telegram audiences are never sendable actions."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import json

import pytest

from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.femdom_research_comment_preview import (
    _PRODUCT,
    _PROJECT,
    _Draft,
    prepare_review,
)
from app.runtime_store import MemoryRuntimeStateStore


def _setup(*, now=None, comment="AVAILABLE", age_minutes=3, kind="CHANNEL"):
    now = now or datetime.now(UTC)
    store = MemoryRuntimeStateStore()
    store.put("customer_acquisition_projects", str(_PROJECT), {
        "id": str(_PROJECT), "product_id": str(_PRODUCT),
        "channel_preferences": {"TELEGRAM": "OFF"},
        "research_state": "READY",
    })
    posttime = now - timedelta(minutes=age_minutes)
    opportunity = DistributionOpportunityView(
        id=uuid4(), icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind(kind),
        canonical_key="telegram:1642699396:community",
        title="Атмосфера", url="https://t.me/a_sfera",
        metadata={
            "handle": "a_sfera", "telegram_entity_id": 1642699396,
            "native_research_status": "VERIFIED",
            "research_audience_status": "REVIEW_REQUIRED",
            "surface_capabilities": {
                "comment": comment, "reply": comment,
                "publisher_permission_verified": False,
            },
            "research_last_native_checked_at": now.isoformat(),
            "research_latest_post_url": "https://t.me/a_sfera/2117",
            "research_latest_post_id": 2117,
            "research_latest_post_published_at": posttime.isoformat(),
            "research_latest_post_excerpt": (
                "Анонсируем встречу по теме БДСМ. Обсуждаем формат мероприятия, "
                "правила уважительного общения и границы участников."
            ),
            "actionability": "RESEARCH_ONLY", "action_target_url": None,
            "action_target_specific": False,
        },
    )
    store.put("audience_distribution_map", str(_PRODUCT), AudienceDistributionMapView(
        product_id=_PRODUCT, top_icp_count=1, opportunity_count=1,
        opportunities=[opportunity],
    ).model_dump(mode="json"))
    product = SimpleNamespace(
        id=_PRODUCT, name="FemDom",
        description="Community for adults interested in FemDom",
        problem_or_desire="Responsible and respectful adult connections",
    )
    return store, product, now


@pytest.mark.asyncio
async def test_nonpublishing_preview_for_adjacent_audience_without_channel_toggle():
    store, product, now = _setup()
    calls = []

    async def judge(system, prompt):
        calls.append(prompt)
        assert "Return a single structured" in system
        assert "language" in system
        assert "GROUP_REPLY" in system
        assert "CHANNEL_COMMENT" in system
        assert "checked separately by the publisher" in system
        assert "Separate topicality" in system
        return _Draft(
            topicality="RELEVANT",
            reason="The post discusses respectful adult community gatherings.",
            language="Russian",
            comment=(
                "Вы отдельно упомянули границы участников. "
                "А формат обсуждения можно узнать заранее?"
            ),
            why_relevant="Responds to a real detail about community boundaries.",
        )

    first = await prepare_review(
        store, product=product, handle="a_sfera", judge=judge, now=now
    )
    assert first["status"] == "AWAITING_HUMAN_CONTEXT_AND_POLICY_REVIEW"
    assert first["target_url"] == "https://t.me/a_sfera/2117"
    assert first["action_sendable"] is False
    assert first["published"] is False
    assert first["audience_status"] == "REVIEW_REQUIRED"
    assert first["publisher_permission_verified"] is False
    assert first["community_commercial_rules_verified"] is False
    assert first["project_telegram_preference"] == "OFF"
    assert first["comment"].startswith("Вы отдельно упомянули")
    assert first["comment_language"] == "Russian"
    assert first["prompt_version"] == "community_message_v4_grounded_target"
    assert "variants" not in first
    assert first["surface"] == "CHANNEL_COMMENT"
    second = await prepare_review(
        store, product=product, handle="a_sfera", judge=judge, now=now
    )
    assert second["cache_hit"] is True
    assert len(calls) == 1
    assert store.list_namespace("distribution_action") == []


@pytest.mark.asyncio
async def test_unrelated_post_returns_no_variants_without_sendability():
    store, product, now = _setup()
    async def judge(_system, _prompt):
        return _Draft(
            topicality="UNRELATED",
            reason="The post is unrelated to the adult community interests.",
            language="Russian",
            comment=None,
        )
    out = await prepare_review(
        store, product=product, handle="a_sfera", judge=judge, now=now
    )
    assert out["comment"] is None
    assert out["action_sendable"] is False


@pytest.mark.asyncio
async def test_stale_and_noncommentable_context_fail_closed_before_model_call():
    called = []

    async def judge(_system, _prompt):
        called.append(1)
        raise AssertionError("Should not call model")

    for opts in ({"age_minutes": 25 * 24 * 60}, {"comment": "UNAVAILABLE"}):
        store, product, now = _setup(**opts)
        with pytest.raises(ValueError):
            await prepare_review(
                store, product=product, handle="a_sfera",
                judge=judge, now=now,
            )
    assert called == []


@pytest.mark.asyncio
async def test_review_scope_rejects_unknown_peers_even_if_product_matches():
    store, product, now = _setup()
    with pytest.raises(ValueError, match="bounded review"):
        await prepare_review(
            store, product=product, handle="unknown_peer", now=now,
        )


@pytest.mark.asyncio
async def test_group_reply_generates_one_native_language_message_without_send():
    store, product, now = _setup(kind="GROUP")
    async def judge(system, prompt):
        assert "GROUP_REPLY" in prompt
        assert "reply to a group message" in system
        return _Draft(
            topicality="RELEVANT",
            reason="The latest group message discusses an event and its ground rules.",
            language="Russian",
            comment="Уточните, пожалуйста, будут ли правила участия объяснять до начала встречи?",
            why_relevant="Grounded in the group's latest native message.",
        )
    output = await prepare_review(
        store, product=product, handle="a_sfera", judge=judge, now=now
    )
    assert output["surface"] == "GROUP_REPLY"
    assert output["comment_language"] == "Russian"
    assert output["comment"].startswith("Уточните")
    assert output["published"] is False
    assert output["action_sendable"] is False


@pytest.mark.asyncio
async def test_no_extra_variant_even_when_model_judge_provides_single_reply():
    store, product, now = _setup()
    calls = []
    async def judge(_system, _prompt):
        calls.append("model")
        return {
            "topicality": "RELEVANT",
            "reason": "Grounded in a specific community event detail.",
            "language": "Russian",
            "comment": "Вы обсуждаете формат встречи. Он уже определён или участники смогут его обсудить?",
            "why_relevant": "The event mentions participant boundaries.",
        }
    first = await prepare_review(
        store, product=product, handle="a_sfera", judge=judge, now=now
    )
    second = await prepare_review(
        store, product=product, handle="a_sfera", judge=judge, now=now
    )
    assert len(calls) == 1
    assert first["comment"] == second["comment"]
    assert "variants" not in first


@pytest.mark.asyncio
async def test_preview_preserves_late_native_post_details_without_cutting_json():
    store, product, now = _setup()
    audience = store.get("audience_distribution_map", str(_PRODUCT))
    opportunity = audience["opportunities"][0]
    native_text = (
        "Обсуждение встречи. " + "Подробности контекста. " * 24
        + " Организаторы предлагают открытый формат вопросов от участников."
    )
    opportunity["metadata"]["recent_context"] = [{
        "message_id": 2117,
        "url": "https://t.me/a_sfera/2117",
        "text": native_text,
    }]
    store.put("audience_distribution_map", str(_PRODUCT), audience)

    async def judge(_system, prompt):
        payload = json.loads(prompt)
        assert "открытый формат вопросов" in payload["selected_target"]["text"]
        assert len(payload["selected_target"]["text"]) > 400
        assert len(payload["selected_target"]["text"]) <= 900
        return _Draft(
            topicality="RELEVANT",
            reason="The newest post explicitly offers an open question format.",
            language="Russian",
            comment="Открытый формат вопросов звучит полезно. Их можно задать заранее?",
        )

    result = await prepare_review(
        store, product=product, handle="a_sfera", judge=judge, now=now
    )
    assert result["target_context_source"] == "verified_native_latest_message"
    assert result["target_context_chars"] > 400
    assert result["published"] is False


@pytest.mark.asyncio
async def test_preview_rejects_native_snapshot_newer_than_saved_target():
    store, product, now = _setup()
    audience = store.get("audience_distribution_map", str(_PRODUCT))
    audience["opportunities"][0]["metadata"]["recent_context"] = [{
        "message_id": 2118,
        "url": "https://t.me/a_sfera/2118",
        "text": "Уже появилось новое сообщение с другими условиями встречи.",
    }]
    store.put("audience_distribution_map", str(_PRODUCT), audience)

    async def judge(_system, _prompt):
        raise AssertionError("A stale post must not reach the model")

    with pytest.raises(ValueError, match="differs from the saved target"):
        await prepare_review(
            store, product=product, handle="a_sfera", judge=judge, now=now
        )
