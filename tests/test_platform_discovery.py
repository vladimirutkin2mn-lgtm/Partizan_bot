from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.audience_intelligence import AudienceIntelligenceEngine
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.platform_discovery import (
    InstagramDiscoveryAdapter,
    PlatformDiscoveryRequest,
    RedditDiscoveryAdapter,
    TelegramDiscoveryAdapter,
    TikTokDiscoveryAdapter,
)
from app.search import DiscoveryQuery, SearchHit, SearchProvider, SourceClass


def _hit(url: str, *, snippet: str = "relationship advice", query: str = "q") -> SearchHit:
    return SearchHit(
        title="Relationship evidence",
        url=url,
        snippet=snippet,
        query=query,
        source_class=SourceClass.COMMUNITY,
    )


def _request(platform, kind, query="q", topic=None) -> PlatformDiscoveryRequest:
    return PlatformDiscoveryRequest(
        platform=platform,
        kind=kind,
        discovery_query=DiscoveryQuery(SourceClass.COMMUNITY, query),
        topic=topic,
    )


def test_telegram_adapter_normalizes_public_surfaces_and_rejects_invites() -> None:
    adapter = TelegramDiscoveryAdapter()
    request = _request(
        DistributionPlatform.TELEGRAM,
        OpportunityKind.CHANNEL,
    )

    candidates = adapter.candidates(
        request,
        [
            _hit("https://t.me/s/relationship_daily/123"),
            _hit("https://t.me/+privateInvite"),
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].canonical_key == "channel:relationship_daily"
    assert candidates[0].url == "https://t.me/relationship_daily"
    assert candidates[0].metadata["surface_kind"] == "CHANNEL"


def test_instagram_adapter_keeps_creator_as_opportunity_not_reel() -> None:
    adapter = InstagramDiscoveryAdapter()
    request = _request(
        DistributionPlatform.INSTAGRAM,
        OpportunityKind.CREATOR_ACCOUNT,
    )

    candidates = adapter.candidates(
        request,
        [
            _hit("https://www.instagram.com/relationshipcoach/"),
            _hit("https://www.instagram.com/reel/ABC123/"),
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].canonical_key == "creator:relationshipcoach"
    assert candidates[0].title == "@relationshipcoach"
    assert candidates[0].metadata["account_handle"] == "relationshipcoach"


def test_reddit_adapter_collapses_thread_and_collects_policy_evidence() -> None:
    adapter = RedditDiscoveryAdapter()
    request = _request(
        DistributionPlatform.REDDIT,
        OpportunityKind.SUBREDDIT,
    )

    candidates = adapter.candidates(
        request,
        [
            _hit(
                "https://www.reddit.com/r/relationships/comments/abc/thread/",
                snippet="Read the community rules before self-promotion or posting links.",
            )
        ],
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.canonical_key == "subreddit:relationships"
    assert candidate.url == "https://www.reddit.com/r/relationships/"
    assert candidate.metadata["subreddit"] == "relationships"
    assert candidate.metadata["policy_evidence"]
    matched = candidate.metadata["policy_evidence"][0]["matched_terms"]
    assert "rules" in matched
    assert "self-promotion" in matched
    assert "links" in matched


def test_tiktok_adapter_aggregates_topic_evidence_instead_of_video_opportunities() -> None:
    adapter = TikTokDiscoveryAdapter()
    request = _request(
        DistributionPlatform.TIKTOK,
        OpportunityKind.CONTENT_CLUSTER,
        topic="breakup advice",
    )

    candidates = adapter.candidates(
        request,
        [
            _hit(
                "https://www.tiktok.com/@relationshipcoach/video/1001",
                snippet="#breakuptok relationship advice",
            ),
            _hit(
                "https://www.tiktok.com/@datingtips/video/1002",
                snippet="#datingtips breakup recovery",
            ),
        ],
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.kind == OpportunityKind.CONTENT_CLUSTER
    assert candidate.url is None
    assert candidate.canonical_key == "topic:breakup-advice"
    assert candidate.metadata["evidence_count"] == 2
    assert candidate.metadata["creator_handles"] == ["@datingtips", "@relationshipcoach"]
    assert set(candidate.metadata["hashtags"]) == {"#breakuptok", "#datingtips"}


class OnePlatformFailsProvider(SearchProvider):
    async def search(
        self,
        discovery_query: DiscoveryQuery,
        limit: int = 5,
    ) -> list[SearchHit]:
        query = discovery_query.query
        if "site:instagram.com" in query:
            raise RuntimeError("Instagram discovery provider unavailable")
        if "site:t.me" in query:
            url = (
                "https://t.me/relationship_chat"
                if "group chat" in query
                else "https://t.me/relationship_daily"
            )
        elif "site:reddit.com/r/" in query:
            url = "https://www.reddit.com/r/relationships/"
        elif "site:tiktok.com" in query:
            url = "https://www.tiktok.com/@relationshipcoach/video/1001"
        else:
            return []
        return [
            SearchHit(
                title="Relationship advice",
                url=url,
                snippet="relationship uncertainty breakup advice",
                query=query,
                source_class=discovery_query.source_class,
            )
        ][:limit]


@pytest.mark.asyncio
async def test_engine_degrades_partially_when_one_platform_provider_fails() -> None:
    product = SimpleNamespace(market="US", language="English")
    icp = SimpleNamespace(
        id=uuid4(),
        title="Relationship advice seekers",
        description="People seeking relationship clarity",
        pain="relationship uncertainty",
        trigger="breakup questions",
        alternatives=["tarot"],
    )
    engine = AudienceIntelligenceEngine(OnePlatformFailsProvider())

    opportunities = await engine.discover(product, [icp])

    platforms = {item.platform for item in opportunities}
    assert DistributionPlatform.INSTAGRAM not in platforms
    assert {
        DistributionPlatform.TELEGRAM,
        DistributionPlatform.REDDIT,
        DistributionPlatform.TIKTOK,
    }.issubset(platforms)
    assert len(engine.last_failures) == 1
    assert engine.last_failures[0].platform == DistributionPlatform.INSTAGRAM
    assert engine.last_failures[0].error_type == "RuntimeError"
