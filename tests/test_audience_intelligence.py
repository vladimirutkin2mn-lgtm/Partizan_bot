from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.audience_intelligence import AudienceIntelligenceEngine
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.search import DiscoveryQuery, SearchHit, SearchProvider


class PlatformAwareSearchProvider(SearchProvider):
    async def search(self, discovery_query: DiscoveryQuery, limit: int = 5) -> list[SearchHit]:
        query = discovery_query.query
        if "site:t.me" in query:
            slug = "relationship_chat" if "group chat" in query else "relationship_daily"
            urls = [f"https://t.me/{slug}"]
        elif "site:instagram.com" in query:
            urls = ["https://www.instagram.com/relationshipcoach/"]
        elif "site:reddit.com/r/" in query:
            urls = ["https://www.reddit.com/r/relationships/comments/abc/example_thread/"]
        elif "site:tiktok.com" in query:
            urls = [
                "https://www.tiktok.com/@relationshipcoach/video/1001",
                "https://www.tiktok.com/@datingtips/video/1002",
            ]
        else:
            urls = []
        return [
            SearchHit(
                title=f"Evidence for {query}",
                url=url,
                snippet=f"Relevant relationship advice evidence for {query}",
                query=query,
                source_class=discovery_query.source_class,
            )
            for url in urls[:limit]
        ]


@pytest.mark.asyncio
async def test_discovery_returns_only_four_mvp_platforms() -> None:
    product = SimpleNamespace(market="US", language="English")
    icp = SimpleNamespace(
        id=uuid4(),
        title="Women seeking relationship clarity",
        description="People looking for perspective on uncertain relationships",
        pain="relationship uncertainty",
        trigger="breakup or mixed signals",
        alternatives=["tarot", "dating advice"],
    )
    engine = AudienceIntelligenceEngine(PlatformAwareSearchProvider())

    opportunities = await engine.discover(product, [icp])

    assert {item.platform for item in opportunities} == {
        DistributionPlatform.TELEGRAM,
        DistributionPlatform.INSTAGRAM,
        DistributionPlatform.REDDIT,
        DistributionPlatform.TIKTOK,
    }
    assert all(item.evidence for item in opportunities)


@pytest.mark.asyncio
async def test_discovery_uses_platform_specific_persistent_units() -> None:
    product = SimpleNamespace(market="US", language="English")
    icp = SimpleNamespace(
        id=uuid4(),
        title="Relationship advice seekers",
        description="People seeking useful relationship guidance",
        pain="relationship uncertainty",
        trigger="breakup questions",
        alternatives=[],
    )
    engine = AudienceIntelligenceEngine(PlatformAwareSearchProvider())

    opportunities = await engine.discover(product, [icp])
    pairs = {(item.platform, item.kind) for item in opportunities}

    assert (DistributionPlatform.TELEGRAM, OpportunityKind.CHANNEL) in pairs
    assert (DistributionPlatform.TELEGRAM, OpportunityKind.GROUP) in pairs
    assert (DistributionPlatform.INSTAGRAM, OpportunityKind.CREATOR_ACCOUNT) in pairs
    assert (DistributionPlatform.REDDIT, OpportunityKind.SUBREDDIT) in pairs
    assert (DistributionPlatform.TIKTOK, OpportunityKind.CONTENT_CLUSTER) in pairs


@pytest.mark.asyncio
async def test_reddit_thread_is_collapsed_to_subreddit_opportunity() -> None:
    product = SimpleNamespace(market="US", language="English")
    icp = SimpleNamespace(
        id=uuid4(),
        title="Relationship advice seekers",
        description="People seeking useful relationship guidance",
        pain="relationship uncertainty",
        trigger="breakup questions",
        alternatives=[],
    )
    engine = AudienceIntelligenceEngine(PlatformAwareSearchProvider())

    opportunities = await engine.discover(product, [icp])
    reddit = next(item for item in opportunities if item.platform == DistributionPlatform.REDDIT)

    assert reddit.canonical_key == "subreddit:relationships"
    assert str(reddit.url) == "https://www.reddit.com/r/relationships/"
    assert reddit.title == "r/relationships"


@pytest.mark.asyncio
async def test_tiktok_hits_are_aggregated_into_topic_clusters() -> None:
    product = SimpleNamespace(market="US", language="English")
    icp = SimpleNamespace(
        id=uuid4(),
        title="Relationship advice seekers",
        description="People seeking useful relationship guidance",
        pain="relationship uncertainty",
        trigger="breakup questions",
        alternatives=[],
    )
    engine = AudienceIntelligenceEngine(PlatformAwareSearchProvider())

    opportunities = await engine.discover(product, [icp])
    tiktok = [item for item in opportunities if item.platform == DistributionPlatform.TIKTOK]

    assert len(tiktok) == 3
    assert all(item.kind == OpportunityKind.CONTENT_CLUSTER for item in tiktok)
    assert all(item.url is None for item in tiktok)
    assert all(len(item.evidence) == 2 for item in tiktok)
    assert all(item.canonical_key.startswith("topic:") for item in tiktok)
