from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.audience_intelligence import AudienceIntelligenceEngine
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.search import DiscoveryQuery, SearchHit, SearchProvider, SourceClass


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


def _icp() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        title="Women seeking relationship clarity",
        description="People looking for perspective on uncertain relationships",
        pain="relationship uncertainty",
        trigger="breakup or mixed signals",
        alternatives=["tarot", "dating advice"],
    )


@pytest.mark.asyncio
async def test_discovery_returns_only_four_mvp_platforms() -> None:
    product = SimpleNamespace(market="US", language="English")
    engine = AudienceIntelligenceEngine(PlatformAwareSearchProvider())

    opportunities = await engine.discover(product, [_icp()])

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


def test_search_query_terms_are_provenance_not_observed_evidence() -> None:
    icp = _icp()
    query = (
        "site:reddit.com/r/ women seeking relationship clarity relationship uncertainty "
        "breakup mixed signals tarot"
    )
    hit = SearchHit(
        title="Public community page",
        url="https://www.reddit.com/r/example/",
        snippet="A general discussion community.",
        query=query,
        source_class=SourceClass.COMMUNITY,
    )
    engine = AudienceIntelligenceEngine(PlatformAwareSearchProvider())

    score, rationale, summary = engine._score(icp, [hit], DistributionPlatform.REDDIT)

    assert score < 20
    assert summary.matched_terms == ()
    assert summary.demand_intent_hits == 0
    assert summary.commercial_intent_hits == 0
    assert summary.confidence == "LOW"
    assert "Search-query terms are provenance only" in rationale


def test_observed_problem_and_intent_signals_raise_confidence() -> None:
    icp = _icp()
    hits = [
        SearchHit(
            title="Relationship uncertainty discussion",
            url="https://example.com/thread-1",
            snippet=(
                "I'm struggling with relationship uncertainty and looking for an alternative. "
                "Which service is worth paying for?"
            ),
            query="discovery query",
            source_class=SourceClass.COMMUNITY,
        ),
        SearchHit(
            title="Relationship advice recommendations",
            url="https://example.com/thread-2",
            snippet=(
                "Need help after mixed signals. Any recommendation for a subscription or trial "
                "that gives relationship clarity?"
            ),
            query="another discovery query",
            source_class=SourceClass.COMMUNITY,
        ),
    ]
    engine = AudienceIntelligenceEngine(PlatformAwareSearchProvider())

    score, _, summary = engine._score(icp, hits, DistributionPlatform.REDDIT)

    assert score > 40
    assert summary.pain_ratio > 0
    assert summary.trigger_ratio > 0
    assert summary.demand_intent_hits == 2
    assert summary.commercial_intent_hits == 2
    assert summary.independent_evidence_count == 2
    assert summary.confidence == "MEDIUM"
    assert {"pain", "trigger", "demand_intent", "commercial_intent"}.issubset(
        set(summary.observed_signal_tags)
    )


@pytest.mark.asyncio
async def test_discovery_persists_research_signal_provenance() -> None:
    product = SimpleNamespace(market="US", language="English")
    engine = AudienceIntelligenceEngine(PlatformAwareSearchProvider())

    opportunities = await engine.discover(product, [_icp()])
    opportunity = opportunities[0]
    signals = opportunity.metadata["research_signals"]

    assert signals["query_terms_count_as_evidence"] is False
    assert "confidence" in signals
    assert opportunity.metadata["marketing_intelligence_skills"] == [
        "customer-research",
        "prospecting",
        "community-marketing",
    ]
    assert all("signal_tags" in evidence for evidence in opportunity.evidence)
