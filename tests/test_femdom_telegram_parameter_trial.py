"""The parameter trial must not alter production prompts, cache, or publisher."""
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.distribution_schemas import (
    AudienceDistributionMapView, DistributionOpportunityView,
)
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.femdom_research_comment_preview import _PRODUCT, _PROJECT, _REVIEW_NAMESPACE
from app.femdom_telegram_parameter_trial import _PROFILE, run
from app.runtime_store import MemoryRuntimeStateStore


def fixture():
    now = datetime.now(UTC)
    store = MemoryRuntimeStateStore()
    store.put("customer_acquisition_projects", str(_PROJECT), {
        "id": str(_PROJECT), "product_id": str(_PRODUCT),
        "channel_preferences": {"TELEGRAM": "OFF"},
    })
    store.put("audience_distribution_map", str(_PRODUCT),
        AudienceDistributionMapView(
            product_id=_PRODUCT, top_icp_count=1, opportunity_count=1,
            opportunities=[DistributionOpportunityView(
                id=uuid4(), icp_id=uuid4(),
                platform=DistributionPlatform.TELEGRAM,
                kind=OpportunityKind.CHANNEL,
                canonical_key="telegram:1642699396:community",
                title="Атмосфера", url="https://t.me/a_sfera",
                metadata={
                    "handle": "a_sfera",
                    "native_research_status": "VERIFIED",
                    "research_audience_status": "REVIEW_REQUIRED",
                    "surface_capabilities": {"comment": "AVAILABLE"},
                    "research_last_native_checked_at": now.isoformat(),
                    "research_latest_post_url": "https://t.me/a_sfera/2117",
                    "research_latest_post_id": 2117,
                    "research_latest_post_published_at": now.isoformat(),
                    "research_latest_post_excerpt": (
                        "Анонс встречи в чёрном и оранжевом. "
                        "Рекомендательный дресс-код и общение."
                    ),
                },
            )],
        ).model_dump(mode="json")
    )
    product = SimpleNamespace(id=_PRODUCT, name="FemDom",
        description="Russian adult-themed community",
        problem_or_desire="Explore events and connections")
    return store, product, now


class OneShotClient:
    def __init__(self, *, parsed=True):
        self.calls = []
        self.parsed = parsed
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self.parse))

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if not self.parsed:
            raise ValueError("Simulated unsupported API parameter")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed={
                "topicality": "RELEVANT",
                "reason": "Comment addresses the offered atmosphere.",
                "language": "Russian",
                "comment": "Нравится, когда дресс-код оставляет место фантазии.",
                "why_relevant": "Factual dress code detail.",
            }))],
            usage=SimpleNamespace(prompt_tokens=1600, completion_tokens=50),
        )


@pytest.mark.asyncio
async def test_one_chat_completion_with_exact_sampling_parameters_and_no_mutations():
    store, product, now = fixture()
    store.put(_REVIEW_NAMESPACE, f"{_PROJECT}:a_sfera:2117", {
        "fingerprint": "old", "comment": "DO NOT RETURN STALE DRAFT",
    })
    existing_cache = store.list_namespace(_REVIEW_NAMESPACE)
    client = OneShotClient()
    result = await run(store=store, product=product, api_client=client, now=now)
    assert len(client.calls) == 1
    kwargs = client.calls[0]
    for key in ("temperature", "presence_penalty", "frequency_penalty",
                "reasoning_effort", "max_completion_tokens", "model"):
        assert kwargs[key] == _PROFILE[key]
    assert len(kwargs["messages"]) == 2
    assert kwargs["messages"][0]["role"] == "system"
    assert kwargs["messages"][1]["role"] == "user"
    assert result["comment"].startswith("Нравится")
    assert result["review_cache_bypassed"] is True
    assert result["review_cache_writes"] == 0
    assert store.list_namespace(_REVIEW_NAMESPACE) == existing_cache
    assert result["telegram_sends"] == 0
    assert result["telegram_joins"] == 0
    assert result["publisher_modified"] is False
    assert result["send_allowed"] is False
    assert result["input_tokens"] == 1600


@pytest.mark.asyncio
async def test_unsupported_parameters_never_trigger_fallback_retry():
    store, product, now = fixture()
    client = OneShotClient(parsed=False)
    with pytest.raises(ValueError, match="unsupported API parameter"):
        await run(store=store, product=product, api_client=client, now=now)
    assert len(client.calls) == 1
    assert store.list_namespace(_REVIEW_NAMESPACE) == []


@pytest.mark.asyncio
async def test_another_project_fails_before_model_call():
    store, product, now = fixture()
    client = OneShotClient()
    with pytest.raises(ValueError, match="scope mismatch"):
        await run(store=store, product=product, api_client=client,
                  project_id=uuid4(), now=now)
    assert client.calls == []


@pytest.mark.asyncio
async def test_other_target_cannot_silently_replace_pinned_post():
    store, product, now = fixture()
    raw = store.get("audience_distribution_map", str(_PRODUCT))
    meta = raw["opportunities"][0]["metadata"]
    meta["research_latest_post_url"] = "https://t.me/a_sfera/2118"
    meta["research_latest_post_id"] = 2118
    store.put("audience_distribution_map", str(_PRODUCT), raw)
    client = OneShotClient()
    with pytest.raises(ValueError, match="Unexpected target"):
        await run(store=store, product=product, api_client=client, now=now)
    assert client.calls == []
