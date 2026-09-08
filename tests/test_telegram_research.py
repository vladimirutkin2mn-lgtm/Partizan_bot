from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.audience_intelligence import AudienceIntelligenceEngine
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.platform_discovery import TelegramDiscoveryAdapter
from app.search import DiscoveryQuery, SearchHit, SearchProvider, SourceClass
from app.telegram_research import (
    SurfaceAvailability,
    TelegramCommunitySnapshot,
    TelegramRecentContext,
    TelegramResearchConnector,
    TelegramResearchUnavailableError,
    TelegramSurfaceKind,
)


class FakeTelegramResearchTransport:
    def __init__(self, snapshots: list[TelegramCommunitySnapshot]) -> None:
        self.snapshots = snapshots
        self.calls: list[dict] = []

    async def discover_public(
        self,
        query: str,
        known_handles: list[str],
        *,
        result_limit: int,
        recent_message_limit: int,
    ) -> list[TelegramCommunitySnapshot]:
        self.calls.append(
            {
                "query": query,
                "known_handles": known_handles,
                "result_limit": result_limit,
                "recent_message_limit": recent_message_limit,
            }
        )
        return list(self.snapshots)


class FailingTelegramResearchTransport:
    async def discover_public(
        self,
        query: str,
        known_handles: list[str],
        *,
        result_limit: int,
        recent_message_limit: int,
    ) -> list[TelegramCommunitySnapshot]:
        del query, known_handles, result_limit, recent_message_limit
        raise TelegramResearchUnavailableError("native Telegram session unavailable")


class EmptySearchProvider(SearchProvider):
    async def search(
        self,
        discovery_query: DiscoveryQuery,
        limit: int = 5,
    ) -> list[SearchHit]:
        del discovery_query, limit
        return []


class DuplicateTelegramWebProvider(SearchProvider):
    async def search(
        self,
        discovery_query: DiscoveryQuery,
        limit: int = 5,
    ) -> list[SearchHit]:
        if "site:t.me" not in discovery_query.query:
            return []
        return [
            SearchHit(
                title="Founders discussing bookkeeping automation",
                url="https://t.me/founder_books",
                snippet="Founders ask for bookkeeping automation and accounting help.",
                query=discovery_query.query,
                source_class=SourceClass.COMMUNITY,
            ),
            SearchHit(
                title="Same public community",
                url="https://t.me/s/founder_books/15",
                snippet="More bookkeeping questions from founders.",
                query=discovery_query.query,
                source_class=SourceClass.COMMUNITY,
            ),
        ][:limit]


def _snapshot(
    *,
    entity_id: int = 1001,
    username: str = "founder_books",
    checked_at: datetime | None = None,
    activity_at: datetime | None = None,
) -> TelegramCommunitySnapshot:
    checked = checked_at or datetime(2026, 9, 8, 8, 0, tzinfo=UTC)
    activity = activity_at or datetime(2026, 9, 8, 7, 55, tzinfo=UTC)
    return TelegramCommunitySnapshot(
        entity_id=entity_id,
        username=username,
        title="Founder Bookkeeping",
        kind=TelegramSurfaceKind.GROUP,
        url=f"https://t.me/{username}",
        about="Community for founders comparing bookkeeping and accounting tools.",
        member_count=4200,
        source_checked_at=checked,
        last_activity_at=activity,
        comment_surface=SurfaceAvailability.UNKNOWN,
        reply_surface=SurfaceAvailability.AVAILABLE,
        standalone_post_surface=SurfaceAvailability.AVAILABLE,
        action_target_url=f"https://t.me/{username}/77",
        recent_context=(
            TelegramRecentContext(
                message_id=77,
                text="Which bookkeeping automation works for a solo founder?",
                published_at=activity,
                url=f"https://t.me/{username}/77",
                matched_terms=("bookkeeping", "founder"),
            ),
        ),
    )


def _product_and_icp() -> tuple[SimpleNamespace, SimpleNamespace]:
    product = SimpleNamespace(market="US", language="English")
    icp = SimpleNamespace(
        id=uuid4(),
        title="Solo founders",
        description="Founders running small internet businesses",
        pain="bookkeeping takes too much time",
        trigger="looking for accounting automation",
        alternatives=["spreadsheet", "accountant"],
    )
    return product, icp


@pytest.mark.asyncio
async def test_connector_deduplicates_entity_and_keeps_freshest_snapshot() -> None:
    older = _snapshot(activity_at=datetime(2026, 9, 7, 8, 0, tzinfo=UTC))
    fresher = _snapshot(activity_at=datetime(2026, 9, 8, 8, 0, tzinfo=UTC))
    transport = FakeTelegramResearchTransport([older, fresher])
    connector = TelegramResearchConnector(
        transport,
        result_limit=5,
        known_handle_limit=2,
        recent_message_limit=3,
    )

    result = await connector.discover(
        query="  solo   founders bookkeeping  ",
        known_handles=["@Founder_Books", "founder_books", "other", "ignored"],
    )

    assert result == [fresher]
    assert transport.calls == [
        {
            "query": "solo founders bookkeeping",
            "known_handles": ["founder_books", "other"],
            "result_limit": 5,
            "recent_message_limit": 3,
        }
    ]


@pytest.mark.asyncio
async def test_native_telegram_can_create_community_opportunity_without_web_hit() -> None:
    transport = FakeTelegramResearchTransport([_snapshot()])
    connector = TelegramResearchConnector(transport)
    adapter = TelegramDiscoveryAdapter(
        research_connector=connector,
        use_default_research_connector=False,
    )
    engine = AudienceIntelligenceEngine(EmptySearchProvider(), adapters=[adapter])
    product, icp = _product_and_icp()

    opportunities = await engine.discover(product, [icp])

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.platform == DistributionPlatform.TELEGRAM
    assert opportunity.kind == OpportunityKind.GROUP
    assert opportunity.canonical_key == "telegram:1001"
    assert str(opportunity.url).rstrip("/") == "https://t.me/founder_books"
    assert opportunity.metadata["native_research_status"] == "VERIFIED"
    assert opportunity.metadata["telegram_entity_id"] == 1001
    assert opportunity.metadata["member_count"] == 4200
    assert opportunity.metadata["action_target_url"] == "https://t.me/founder_books/77"
    assert opportunity.metadata["action_target_specific"] is True
    assert opportunity.metadata["surface_capabilities"] == {
        "comment": "UNKNOWN",
        "reply": "AVAILABLE",
        "standalone_post": "AVAILABLE",
        "publisher_permission_verified": False,
    }
    native_evidence = next(
        item
        for item in opportunity.evidence
        if item.get("source_metadata", {}).get("evidence_type") == "telegram_native"
    )
    assert native_evidence["source_metadata"]["last_activity_at"].startswith("2026-09-08")


@pytest.mark.asyncio
async def test_native_enrichment_deduplicates_web_channel_group_guesses_by_entity() -> None:
    connector = TelegramResearchConnector(FakeTelegramResearchTransport([_snapshot()]))
    adapter = TelegramDiscoveryAdapter(
        research_connector=connector,
        use_default_research_connector=False,
    )
    engine = AudienceIntelligenceEngine(DuplicateTelegramWebProvider(), adapters=[adapter])
    product, icp = _product_and_icp()

    opportunities = await engine.discover(product, [icp])

    matching = [item for item in opportunities if item.canonical_key == "telegram:1001"]
    assert len(matching) == 1
    assert matching[0].kind == OpportunityKind.GROUP
    assert matching[0].metadata["surface_kind"] == "GROUP"
    assert matching[0].metadata["native_research_status"] == "VERIFIED"


@pytest.mark.asyncio
async def test_native_failure_keeps_public_web_evidence_but_never_claims_native_verification() -> None:
    connector = TelegramResearchConnector(FailingTelegramResearchTransport())
    adapter = TelegramDiscoveryAdapter(
        research_connector=connector,
        use_default_research_connector=False,
    )
    engine = AudienceIntelligenceEngine(DuplicateTelegramWebProvider(), adapters=[adapter])
    product, icp = _product_and_icp()

    opportunities = await engine.discover(product, [icp])

    assert opportunities
    assert all(item.metadata["native_research_status"] == "NOT_CHECKED" for item in opportunities)
    assert any("native_enrichment" in item.message for item in engine.last_failures)


def test_snapshot_freshness_is_source_timestamp_not_an_invented_activity_signal() -> None:
    activity = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    snapshot = _snapshot(
        checked_at=activity + timedelta(days=7),
        activity_at=activity,
    )

    assert snapshot.source_checked_at > snapshot.last_activity_at
    assert snapshot.recent_context[0].published_at == activity
