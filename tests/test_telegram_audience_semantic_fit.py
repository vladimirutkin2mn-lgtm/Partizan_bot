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
            rationale="Native audience evidence cited", evidence=["Клуб БДСМ"],
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


@pytest.mark.asyncio
async def test_direct_fit_accepts_model_citation_wrapped_in_native_quotes():
    store = MemoryRuntimeStateStore()
    async def judge(_system, _input):
        return AudienceFitJudgment(
            verdict="DIRECT", confidence=0.95,
            rationale="Explicit native audience", evidence=["Community: «Клуб БДСМ»"],
        )
    model = TelegramAudienceSemanticFit(store, enabled=True, judge=judge)
    outcome = await model.evaluate(
        product=_product(), icp=_icp(), handle="a_sfera",
        title="Атмосфера", metadata=_metadata(),
    )
    assert outcome["verdict"] == "DIRECT"
    assert outcome["direct_fit"] is True


@pytest.mark.asyncio
async def test_femdom_v2_calibration_uses_its_own_nine_slot_budget_only_for_allowlist():
    from app.telegram_audience_semantic_fit import (
        _FEMDOM_CALIBRATION_PRODUCT,
        _FEMDOM_V2_CALIBRATION_NAMESPACE,
        AUDIENCE_FIT_BUDGET_NAMESPACE,
    )

    store = MemoryRuntimeStateStore()
    calls = []

    async def fake(_system, prompt):
        calls.append(prompt)
        return AudienceFitJudgment(
            verdict="ADJACENT", confidence=0.9,
            rationale="Related BDSM category", evidence=["Клуб БДСМ"],
        )

    product = _product()
    product.id = _FEMDOM_CALIBRATION_PRODUCT
    fit = TelegramAudienceSemanticFit(
        store, enabled=True, judge=fake,
        femdom_v2_calibration_once=True, daily_max=9,
    )
    other = await fit.evaluate(
        product=product, icp=_icp(), handle="unauthorized_group",
        title="Other", metadata=_metadata(),
    )
    assert other["status"] == "CALIBRATION_SCOPE_BLOCKED"
    for h in (
        "a_sfera", "bizarre_afisha", "fem_tut", "findom_gospoja0",
        "moscowellebdsm1", "play_hard_community", "poiskfanfikov",
        "poiskrabovirabin", "znakomstvachannel",
    ):
        outcome = await fit.evaluate(
            product=product, icp=_icp(), handle=h,
            title=h, metadata=_metadata(),
        )
        assert outcome["status"] == "EVALUATED"
    assert len(calls) == 9
    assert len(store.list_namespace(_FEMDOM_V2_CALIBRATION_NAMESPACE)) == 9
    assert store.list_namespace(AUDIENCE_FIT_BUDGET_NAMESPACE) == []
    blocked = await fit.evaluate(
        product=product, icp=_icp(), handle="a_sfera",
        title="Changed title", metadata=_metadata(),
    )
    assert blocked["status"] == "DAILY_CALL_CAP"
    assert len(calls) == 9


@pytest.mark.asyncio
async def test_cache_only_rerun_never_spends_tokens_even_for_missing_outcome():
    from app.telegram_audience_semantic_fit import _FEMDOM_CALIBRATION_PRODUCT

    store = MemoryRuntimeStateStore()
    calls = []

    async def fake(_system, _prompt):
        calls.append(1)
        return AudienceFitJudgment(
            verdict="UNRELATED", confidence=1,
            rationale="No topical match", evidence=["Клуб БДСМ"],
        )

    product = _product()
    product.id = _FEMDOM_CALIBRATION_PRODUCT
    model = TelegramAudienceSemanticFit(
        store, enabled=True, judge=fake,
        femdom_v2_calibration_once=True, daily_max=9,
    )
    empty = await model.evaluate(
        product=product, icp=_icp(), handle="a_sfera",
        title="Клуб", metadata=_metadata(), cache_only=True,
    )
    assert empty["status"] == "CACHE_MISS"
    assert calls == []
    saved = await model.evaluate(
        product=product, icp=_icp(), handle="a_sfera",
        title="Клуб", metadata=_metadata(),
    )
    assert saved["status"] == "EVALUATED"
    recovered = await model.evaluate(
        product=product, icp=_icp(), handle="a_sfera",
        title="Клуб", metadata=_metadata(), cache_only=True,
    )
    assert recovered["status"] == "CACHED"
    assert len(calls) == 1
