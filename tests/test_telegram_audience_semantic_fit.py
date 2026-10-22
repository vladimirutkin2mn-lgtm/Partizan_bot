"""Bounded audience-fit model calls; neither newest post churn nor promo implies send."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_audience_semantic_fit import (
    AUDIENCE_FIT_CACHE_NAMESPACE,
    AudienceFitJudgment,
    TelegramAudienceSemanticFit,
)
from app.telegram_discovery_strategy import ExpandedTelegramDiscoveryAdapter


def _product():
    return SimpleNamespace(
        id=uuid4(), name="FemDom", description="Niche matching service",
        problem_or_desire="Women-led relationship interests",
        known_audience=["FemDom community"],
    )


def _icp():
    return SimpleNamespace(title="FemDom community", pain="Connections")


def _metadata():
    return {
        "native_research_status": "VERIFIED",
        "about": "Клуб БДСМ и тематические встречи",
        "recent_context": [{
            "message_id": 2116, "text": "Клуб БДСМ. Встреча сегодня!",
            "url": "https://t.me/a_sfera/2116",
        }],
    }


@pytest.mark.asyncio
async def test_bilingual_adjacent_is_not_confirmed_and_is_cached_across_post_changes():
    store = MemoryRuntimeStateStore()
    calls = []

    async def fake(system, prompt):
        calls.append(prompt)
        return AudienceFitJudgment(
            verdict="ADJACENT", confidence=0.97,
            rationale="BDSM club is broader than FemDom-only audience",
            evidence=["Клуб БДСМ"],
        )

    fit = TelegramAudienceSemanticFit(store, enabled=True, judge=fake)
    p, icp, m = _product(), _icp(), _metadata()
    first = await fit.evaluate(product=p, icp=icp, handle="a_sfera",
                               title="Атмосфера", metadata=m)
    assert first["verdict"] == "ADJACENT"
    assert first["direct_fit"] is False
    m["recent_context"][0]["message_id"] = 2117
    m["recent_context"][0]["text"] = "Новый рекламный пост"
    second = await fit.evaluate(product=p, icp=icp, handle="a_sfera",
                                title="Атмосфера", metadata=m)
    assert second["cache_hit"] is True
    assert len(calls) == 1
    assert len(store.list_namespace(AUDIENCE_FIT_CACHE_NAMESPACE)) == 1


@pytest.mark.asyncio
async def test_direct_needs_a_literal_evidence_snippet_and_minimum_confidence():
    store = MemoryRuntimeStateStore()
    p, icp, m = _product(), _icp(), _metadata()
    async def fake(_system, _prompt):
        return AudienceFitJudgment(
            verdict="DIRECT", confidence=0.95,
            rationale="No evidence in message", evidence=["Made up facts"],
        )
    fit = TelegramAudienceSemanticFit(store, enabled=True, judge=fake)
    bad = await fit.evaluate(product=p, icp=icp, handle="a_sfera",
                             title="Атмосфера", metadata=m)
    assert bad["direct_fit"] is False
    async def verified(_system, _prompt):
        return AudienceFitJudgment(
            verdict="DIRECT", confidence=0.9,
            rationale="Explicit audience described", evidence=["FemDom community"],
        )
    second = TelegramAudienceSemanticFit(MemoryRuntimeStateStore(), enabled=True,
                                         judge=verified)
    ok = await second.evaluate(product=p, icp=icp, handle="a_sfera",
                               title="Атмосфера", metadata=m)
    assert ok["direct_fit"] is True


@pytest.mark.asyncio
async def test_no_api_key_or_daily_capacity_is_fail_closed():
    p, icp, m = _product(), _icp(), _metadata()
    store = MemoryRuntimeStateStore()
    async def fake(_system, _prompt):
        return AudienceFitJudgment(
            verdict="DIRECT", confidence=1,
            rationale="Direct", evidence=["FemDom community"],
        )
    disabled = TelegramAudienceSemanticFit(store, enabled=False, judge=fake)
    assert (await disabled.evaluate(
        product=p, icp=icp, handle="a_sfera", title="Sfera", metadata=m
    ))["status"] == "DISABLED"
    zero = TelegramAudienceSemanticFit(store, enabled=True, judge=fake, daily_max=0)
    assert (await zero.evaluate(
        product=p, icp=icp, handle="a_sfera", title="Sfera", metadata=m
    ))["status"] == "DAILY_CALL_CAP"
    assert store.list_namespace(AUDIENCE_FIT_CACHE_NAMESPACE) == []


@pytest.mark.asyncio
async def test_bounded_daily_budget_for_uncached_peers():
    p, icp, m = _product(), _icp(), _metadata()
    store = MemoryRuntimeStateStore()
    count = []
    async def fake(_system, prompt):
        count.append(prompt)
        return AudienceFitJudgment(
            verdict="UNRELATED", confidence=1, rationale="Different group",
            evidence=["Клуб БДСМ"],
        )
    fit = TelegramAudienceSemanticFit(store, enabled=True, judge=fake, daily_max=2)
    for handle in ("a_sfera", "play_hard_community"):
        result = await fit.evaluate(product=p, icp=icp, handle=handle,
                                    title=handle, metadata=m)
        assert result["status"] == "EVALUATED"
    third = await fit.evaluate(product=p, icp=icp, handle="third",
                               title="third", metadata=m)
    assert third["status"] == "DAILY_CALL_CAP"
    assert len(count) == 2


def test_promotional_announcement_can_be_considered_but_no_write_permission():
    from app.telegram_research import TelegramRecentContext

    item = TelegramRecentContext(
        message_id=1, text="FemDom встреча: акция, скидка, записаться! t.me/example",
        published_at=datetime.now(UTC) - timedelta(minutes=10),
        url="https://t.me/test/1",
    )
    ready, reason, signals, matched = (
        ExpandedTelegramDiscoveryAdapter._conversation_readiness(
            item, {"femdom"}
        )
    )
    assert ready is True
    assert reason == "PROMOTIONAL_CONTEXT"
    assert "PROMOTIONAL_POST" in signals
    assert matched == ["femdom"]

    unrelated = TelegramRecentContext(
        message_id=2, text="Скидка на обувь! t.me/shop",
        published_at=datetime.now(UTC), url="https://t.me/test/2",
    )
    assert ExpandedTelegramDiscoveryAdapter._conversation_readiness(
        unrelated, {"femdom"}
    )[0] is False
