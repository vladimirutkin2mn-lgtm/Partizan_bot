from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.community_first_audience_engine import (
    GROWTH_MANDATE_NAMESPACE,
    CommunityFirstAudienceIntelligenceEngine,
)
from app.community_first_telegram_adapter import (
    COMMUNITY_FIRST_TELEGRAM_QUERY_BUDGET,
    CommunityFirstTelegramDiscoveryAdapter,
)
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.platform_discovery import PlatformDiscoveryRequest
from app.runtime_store import MemoryRuntimeStateStore
from app.search import DiscoveryQuery, MockSearchProvider, SearchHit, SourceClass
from app.telegram_research import (
    SurfaceAvailability,
    TelegramCommunitySnapshot,
    TelegramRecentContext,
    TelegramSurfaceKind,
)


def _product(product_id=None):
    return SimpleNamespace(
        id=product_id or uuid4(),
        name="FemDom",
        problem_or_desire="знакомство с femdom и BDSM",
        value_proposition="безопасно найти близкое по интересам сообщество",
        description="FemDom product for people exploring BDSM dynamics",
        known_audience=["новички в BDSM", "люди с интересом к femdom"],
        market="Russia",
        language="Russian",
    )


def _icp():
    return SimpleNamespace(
        id=uuid4(),
        rank=1,
        title="Люди, которые ищут FemDom сообщества",
        description="Русскоязычная BDSM аудитория",
        pain="сложно найти подходящее сообщество",
        trigger="ищет новое место для общения",
        desired_outcome="найти тематическое сообщество",
        alternatives=["BDSM чаты", "тематические знакомства"],
    )


def _request() -> PlatformDiscoveryRequest:
    return PlatformDiscoveryRequest(
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.CHANNEL,
        topic="FemDom BDSM знакомства",
        discovery_query=DiscoveryQuery(
            SourceClass.COMMUNITY,
            "site:t.me FemDom BDSM знакомства Telegram channel community",
        ),
    )


def _hit(url: str, title: str = "FemDom community") -> SearchHit:
    return SearchHit(
        title=title,
        url=url,
        snippet="FemDom BDSM знакомства и тематическое общение",
        query=_request().discovery_query.query,
        source_class=SourceClass.COMMUNITY,
    )


def _snapshot(*, linked_discussion_id=99) -> TelegramCommunitySnapshot:
    old = datetime.now(UTC) - timedelta(days=45)
    newer = old + timedelta(days=2)
    return TelegramCommunitySnapshot(
        entity_id=42,
        username="play_hard_community",
        title="Play.Hard FemDom community",
        kind=TelegramSurfaceKind.CHANNEL,
        url="https://t.me/play_hard_community",
        about="BDSM знакомства и тематическое сообщество",
        member_count=3000,
        source_checked_at=datetime.now(UTC),
        last_activity_at=newer,
        comment_surface=(
            SurfaceAvailability.AVAILABLE
            if linked_discussion_id is not None
            else SurfaceAvailability.UNAVAILABLE
        ),
        reply_surface=SurfaceAvailability.UNKNOWN,
        standalone_post_surface=SurfaceAvailability.UNAVAILABLE,
        linked_discussion_id=linked_discussion_id,
        recent_context=(
            TelegramRecentContext(
                message_id=147,
                text="Новый пост сообщества",
                published_at=newer,
                url="https://t.me/play_hard_community/147",
            ),
            TelegramRecentContext(
                message_id=111,
                text="Старый пост сообщества",
                published_at=old,
                url="https://t.me/play_hard_community/111",
            ),
        ),
    )


def test_query_plan_is_community_first_and_bounded() -> None:
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        use_default_research_connector=False,
        store=MemoryRuntimeStateStore(),
    )

    requests = adapter.build_requests(_product(), _icp())

    assert len(requests) == COMMUNITY_FIRST_TELEGRAM_QUERY_BUDGET == 3
    assert all(request.platform == DistributionPlatform.TELEGRAM for request in requests)
    assert all("site:t.me" in request.discovery_query.query for request in requests)


def test_two_posts_from_same_channel_become_one_candidate() -> None:
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        use_default_research_connector=False,
        store=MemoryRuntimeStateStore(),
    )

    candidates = adapter.candidates(
        _request(),
        [
            _hit("https://t.me/s/play_hard_community/147?utm_source=openai"),
            _hit("https://t.me/s/play_hard_community/111?utm_source=openai"),
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].canonical_key == "telegram:play_hard_community:community"
    assert candidates[0].url == "https://t.me/play_hard_community"
    assert len(candidates[0].hits) == 2


def test_invite_link_is_kept_as_join_required_community() -> None:
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        use_default_research_connector=False,
        store=MemoryRuntimeStateStore(),
    )

    candidates = adapter.candidates(
        _request(),
        [_hit("https://t.me/+JfCxNbvTc35mY2I0?utm_source=openai")],
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.metadata["access_state"] == "JOIN_REQUIRED"
    assert candidate.metadata["actionability"] == "JOIN_REQUIRED"
    assert candidate.url == "https://t.me/+JfCxNbvTc35mY2I0"


def test_latest_post_is_selected_even_when_it_is_old() -> None:
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        use_default_research_connector=False,
        store=MemoryRuntimeStateStore(),
    )

    metadata = adapter._native_metadata(_request(), _snapshot())

    assert metadata["action_target_specific"] is True
    assert metadata["action_target_url"] == "https://t.me/play_hard_community/147"
    assert metadata["action_target_resolution"] == "LATEST_COMMUNITY_TARGET"
    assert metadata["action_target_quality"] == "LATEST_COMMUNITY_SURFACE"
    assert metadata["target_age_days"] > 30


def test_known_no_discussion_latest_post_does_not_fall_back_for_same_product() -> None:
    store = MemoryRuntimeStateStore()
    product = _product()
    project_id = uuid4()
    store.put(
        "customer_acquisition_projects",
        str(project_id),
        {"id": str(project_id), "product_id": str(product.id)},
    )
    store.put(
        "customer_live_opportunities",
        f"{project_id}:bad-target",
        {
            "project_id": str(project_id),
            "opportunity_id": "bad-target",
            "url": "https://t.me/play_hard_community/147",
            "publishability": "NO_DISCUSSION",
        },
    )
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        use_default_research_connector=False,
        store=store,
    )
    adapter.build_requests(product, _icp())

    metadata = adapter._native_metadata(_request(), _snapshot())

    assert metadata["action_target_url"] is None
    assert metadata["community_only_reason"] == "LATEST_POST_BLOCKED"
    assert metadata["actionability"] == "RESEARCH_ONLY"


@pytest.mark.asyncio
async def test_bot_handle_is_not_returned_as_community_opportunity() -> None:
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        use_default_research_connector=False,
        store=MemoryRuntimeStateStore(),
    )
    candidates = adapter.candidates(
        _request(),
        [_hit("https://t.me/sinscity_bot", title="Telegram: Launch @sinscity_bot")],
    )

    enriched = await adapter.enrich_candidates(_request(), candidates)

    assert enriched == []


@pytest.mark.asyncio
async def test_active_mandate_limits_search_to_enabled_platforms() -> None:
    store = MemoryRuntimeStateStore()
    product = _product()
    store.put(
        GROWTH_MANDATE_NAMESPACE,
        str(product.id),
        {
            "product_id": str(product.id),
            "status": "ACTIVE",
            "allowed_platforms": ["TELEGRAM"],
        },
    )
    engine = CommunityFirstAudienceIntelligenceEngine(
        MockSearchProvider(),
        store=store,
    )

    await engine.discover(product, [_icp()], max_opportunities=20)

    assert engine.last_attempts
    assert {attempt.platform for attempt in engine.last_attempts} == {
        DistributionPlatform.TELEGRAM
    }
    assert len(engine.last_attempts) == COMMUNITY_FIRST_TELEGRAM_QUERY_BUDGET


def test_web_search_evidence_is_bound_to_each_annotation() -> None:
    from app.search import OpenAIWebSearchProvider

    text = (
        "Play.Hard is a FemDom/BDSM community for newcomers. "
        "Bizarre is a separate event community with its own audience."
    )
    first_start = text.index("Play.Hard")
    first_end = text.index("newcomers") + len("newcomers")
    second_start = text.index("Bizarre")
    second_end = len(text)
    response = SimpleNamespace(
        output_text=text,
        output=[
            SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text=text,
                        annotations=[
                            SimpleNamespace(
                                url="https://t.me/play_hard_community",
                                title="Play.Hard",
                                start_index=first_start,
                                end_index=first_end,
                            ),
                            SimpleNamespace(
                                url="https://t.me/bizarreclubmsk",
                                title="Bizarre",
                                start_index=second_start,
                                end_index=second_end,
                            ),
                        ],
                    )
                ]
            )
        ],
    )
    provider = OpenAIWebSearchProvider.__new__(OpenAIWebSearchProvider)

    hits = provider._extract_hits(response, _request().discovery_query)

    assert len(hits) == 2
    assert hits[0].metadata["evidence_binding"] == "ANNOTATION_WINDOW"
    assert hits[1].metadata["evidence_binding"] == "ANNOTATION_WINDOW"
    assert hits[0].snippet != hits[1].snippet


def test_web_search_without_offsets_does_not_copy_shared_answer_to_every_hit() -> None:
    from app.search import OpenAIWebSearchProvider

    response = SimpleNamespace(
        output_text="Shared answer mentioning Play.Hard and Bizarre together.",
        output=[
            SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text="Shared answer mentioning Play.Hard and Bizarre together.",
                        annotations=[
                            SimpleNamespace(
                                url="https://t.me/play_hard_community",
                                title="Play.Hard",
                            ),
                            SimpleNamespace(
                                url="https://t.me/bizarreclubmsk",
                                title="Bizarre",
                            ),
                        ],
                    )
                ]
            )
        ],
    )
    provider = OpenAIWebSearchProvider.__new__(OpenAIWebSearchProvider)

    hits = provider._extract_hits(response, _request().discovery_query)

    assert [hit.snippet for hit in hits] == ["Play.Hard", "Bizarre"]
    assert all(hit.metadata["evidence_binding"] == "TITLE_ONLY" for hit in hits)


@pytest.mark.asyncio
async def test_relevant_community_is_persisted_in_registry() -> None:
    from app.community_first_telegram_adapter import TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE

    store = MemoryRuntimeStateStore()
    product = _product()
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        use_default_research_connector=False,
        store=store,
    )
    adapter.build_requests(product, _icp())
    candidates = adapter.candidates(
        _request(),
        [_hit("https://t.me/s/play_hard_community/147")],
    )

    enriched = await adapter.enrich_candidates(_request(), candidates)

    assert enriched[0].metadata["community_fit_pass"] is True
    rows = store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE)
    assert len(rows) == 1
    assert rows[0]["product_id"] == str(product.id)
    assert rows[0]["url"] == "https://t.me/play_hard_community"


@pytest.mark.asyncio
async def test_registered_telegram_community_skips_repeat_web_search() -> None:
    from app.community_first_telegram_adapter import TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE

    class CountingProvider(MockSearchProvider):
        def __init__(self) -> None:
            self.calls = 0

        async def search(self, discovery_query, limit=5):
            self.calls += 1
            return await super().search(discovery_query, limit=limit)

    store = MemoryRuntimeStateStore()
    product = _product()
    icp = _icp()
    store.put(
        GROWTH_MANDATE_NAMESPACE,
        str(product.id),
        {
            "product_id": str(product.id),
            "status": "ACTIVE",
            "allowed_platforms": ["TELEGRAM"],
        },
    )
    store.put(
        TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE,
        f"{product.id}:entity:42",
        {
            "id": f"{product.id}:entity:42",
            "product_id": str(product.id),
            "canonical_key": "telegram:42:community",
            "title": "Play.Hard FemDom community",
            "url": "https://t.me/play_hard_community",
            "kind": "CHANNEL",
            "metadata": {
                "handle": "play_hard_community",
                "telegram_entity_id": 42,
                "source_type": "CHANNEL",
                "native_research_status": "VERIFIED",
                "community_fit_pass": True,
                "community_fit_terms": ["femdom", "bdsm"],
                "opportunity_scope": "COMMUNITY",
                "actionability": "RESEARCH_ONLY",
            },
        },
    )
    provider = CountingProvider()
    engine = CommunityFirstAudienceIntelligenceEngine(provider, store=store)

    opportunities = await engine.discover(product, [icp], max_opportunities=20)

    assert provider.calls == 0
    assert len(opportunities) == 1
    assert opportunities[0].canonical_key == "telegram:42:community"
    assert opportunities[0].metadata["community_fit_reason"] == "REGISTRY_CONFIRMED"


@pytest.mark.asyncio
async def test_empty_active_mandate_runs_no_platform_search() -> None:
    store = MemoryRuntimeStateStore()
    product = _product()
    store.put(
        GROWTH_MANDATE_NAMESPACE,
        str(product.id),
        {
            "product_id": str(product.id),
            "status": "ACTIVE",
            "allowed_platforms": [],
        },
    )
    engine = CommunityFirstAudienceIntelligenceEngine(MockSearchProvider(), store=store)

    opportunities = await engine.discover(product, [_icp()], max_opportunities=20)

    assert opportunities == []
    assert engine.last_attempts == []


@pytest.mark.asyncio
async def test_native_registry_refresh_keeps_confirmed_community_fit() -> None:
    from app.community_first_telegram_adapter import TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE

    class FakeConnector:
        async def discover(self, query, known_handles):
            del query, known_handles
            return [_snapshot()]

    store = MemoryRuntimeStateStore()
    product = _product()
    store.put(
        TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE,
        f"{product.id}:entity:42",
        {
            "id": f"{product.id}:entity:42",
            "product_id": str(product.id),
            "canonical_key": "telegram:42:community",
            "title": "Play.Hard FemDom community",
            "url": "https://t.me/play_hard_community",
            "kind": "CHANNEL",
            "metadata": {
                "handle": "play_hard_community",
                "telegram_entity_id": 42,
                "community_fit_pass": True,
                "community_fit_terms": ["femdom", "bdsm"],
            },
        },
    )
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        research_connector=FakeConnector(),
        store=store,
    )

    refreshed = await adapter.refresh_registered(product, [_icp()])

    assert len(refreshed) == 1
    assert refreshed[0].metadata["registry_confirmed"] is True
    assert refreshed[0].metadata["community_fit_pass"] is True
    assert refreshed[0].metadata["community_fit_reason"] == "REGISTRY_CONFIRMED"
