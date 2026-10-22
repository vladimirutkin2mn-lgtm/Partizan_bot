"""Research previews for adjacent Telegram audiences are never sendable actions."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.femdom_research_comment_preview import (
    _Draft,
    _PRODUCT,
    _PROJECT,
    _Variant,
    prepare_review,
)
from app.runtime_store import MemoryRuntimeStateStore


def _setup(*, now=None, comment="AVAILABLE", age_minutes=3):
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
        kind=OpportunityKind.CHANNEL,
        canonical_key="telegram:1642699396:community",
        title="Атмосфера", url="https://t.me/a_sfera",
        metadata={
            "handle": "a_sfera", "telegram_entity_id": 1642699396,
            "native_research_status": "VERIFIED",
            "research_audience_status": "REVIEW_REQUIRED",
            "surface_capabilities": {
                "comment": comment, "publisher_permission_verified": False,
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
        assert "NON-PUBLISHING" in system
        return _Draft(
            topicality="RELEVANT",
            reason="The post discusses respectful adult community gatherings.",
            variants=[
                _Variant(
                    approach="CONTEXTUAL",
                    text="Хорошо, что в программе отдельно уделили внимание уважительному общению и границам.",
                    why_relevant="The latest post is about respectful community boundaries.",
                ),
                _Variant(
                    approach="TRANSPARENT_INVITATION",
                    text="Развиваем сообщество FemDom для взрослых, нам близок такой подход к уважительному общению.",
                    why_relevant="The latest post discusses boundaries.",
                ),
            ],
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
    assert len(first["variants"]) == 2
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
            variants=[],
        )
    out = await prepare_review(
        store, product=product, handle="a_sfera", judge=judge, now=now
    )
    assert out["variants"] == []
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
