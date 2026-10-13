from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.audience_intelligence_service import InMemoryAudienceIntelligenceService
from app.distribution_schemas import DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.platform_discovery import PlatformCandidate, PlatformDiscoveryRequest
from app.runtime_store import MemoryRuntimeStateStore
from app.search import DiscoveryQuery, SourceClass
from app.telegram_discovery_strategy import ExpandedTelegramDiscoveryAdapter
from app.telegram_research import (
    SurfaceAvailability,
    TelegramCommunitySnapshot,
    TelegramRecentContext,
    TelegramSurfaceKind,
)


class SnapshotConnector:
    def __init__(self, snapshot: TelegramCommunitySnapshot) -> None:
        self.snapshot = snapshot

    async def discover(self, *, query: str, known_handles: list[str]):
        del query, known_handles
        return [self.snapshot]


def _request() -> PlatformDiscoveryRequest:
    return PlatformDiscoveryRequest(
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.CHANNEL,
        topic="FemDom BDSM знакомства безопасное сообщество",
        discovery_query=DiscoveryQuery(
            SourceClass.COMMUNITY,
            "site:t.me FemDom BDSM знакомства безопасное сообщество Telegram channel",
        ),
    )


def _candidate() -> PlatformCandidate:
    return PlatformCandidate(
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.CHANNEL,
        canonical_key="channel:example",
        title="example",
        url="https://t.me/example",
        metadata={"handle": "example", "surface_kind": "CHANNEL"},
        hits=[],
    )


def _snapshot(*, relevant: bool, message_id: int = 123) -> TelegramCommunitySnapshot:
    now = datetime.now(UTC)
    return TelegramCommunitySnapshot(
        entity_id=42,
        username="example",
        title="FemDom community" if relevant else "Python jobs",
        kind=TelegramSurfaceKind.CHANNEL,
        url="https://t.me/example",
        about=(
            "BDSM знакомства, события и безопасные практики"
            if relevant
            else "Backend vacancies and engineering news"
        ),
        member_count=1000,
        source_checked_at=now,
        last_activity_at=now - timedelta(hours=1),
        comment_surface=SurfaceAvailability.AVAILABLE,
        reply_surface=SurfaceAvailability.UNKNOWN,
        standalone_post_surface=SurfaceAvailability.UNAVAILABLE,
        linked_discussion_id=99,
        action_target_url=None,
        recent_context=(
            TelegramRecentContext(
                message_id=message_id,
                text="Сегодня встречаемся вечером. Подробности внутри.",
                published_at=now - timedelta(hours=1),
                url=f"https://t.me/example/{message_id}",
                matched_terms=(),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_community_and_post_get_distinct_persistent_surface_keys() -> None:
    community_adapter = ExpandedTelegramDiscoveryAdapter(
        research_connector=SnapshotConnector(_snapshot(relevant=False)),
        use_default_research_connector=False,
    )
    post_adapter = ExpandedTelegramDiscoveryAdapter(
        research_connector=SnapshotConnector(_snapshot(relevant=True)),
        use_default_research_connector=False,
    )

    community = (await community_adapter.enrich_candidates(_request(), [_candidate()]))[0]
    post = (await post_adapter.enrich_candidates(_request(), [_candidate()]))[0]

    assert community.canonical_key == "telegram:42:community"
    assert community.metadata["opportunity_scope"] == "COMMUNITY"
    assert community.metadata["surface_parent_key"] == "telegram:42"

    assert post.canonical_key == "telegram:42:post:123"
    assert post.metadata["opportunity_scope"] == "POST"
    assert post.metadata["surface_parent_key"] == "telegram:42"
    assert community.canonical_key != post.canonical_key


@pytest.mark.asyncio
async def test_two_posts_in_same_community_remain_distinct_surfaces() -> None:
    first_adapter = ExpandedTelegramDiscoveryAdapter(
        research_connector=SnapshotConnector(_snapshot(relevant=True, message_id=123)),
        use_default_research_connector=False,
    )
    second_adapter = ExpandedTelegramDiscoveryAdapter(
        research_connector=SnapshotConnector(_snapshot(relevant=True, message_id=124)),
        use_default_research_connector=False,
    )

    first = (await first_adapter.enrich_candidates(_request(), [_candidate()]))[0]
    second = (await second_adapter.enrich_candidates(_request(), [_candidate()]))[0]

    assert first.canonical_key == "telegram:42:post:123"
    assert second.canonical_key == "telegram:42:post:124"
    assert first.metadata["surface_parent_key"] == second.metadata["surface_parent_key"]
    assert first.canonical_key != second.canonical_key


def _view(*, icp_id, canonical_key: str, score: float = 80.0) -> DistributionOpportunityView:
    return DistributionOpportunityView(
        id=uuid4(),
        icp_id=icp_id,
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.CHANNEL,
        canonical_key=canonical_key,
        title=canonical_key,
        url="https://t.me/example",
        relevance_score=score,
        rationale="Audience evidence",
        metadata={},
        evidence=[],
    )


def test_adaptive_merge_preserves_same_surface_for_different_icps() -> None:
    service = InMemoryAudienceIntelligenceService(MemoryRuntimeStateStore())
    first = _view(icp_id=uuid4(), canonical_key="telegram:42:community")
    second = _view(icp_id=uuid4(), canonical_key="telegram:42:community")

    merged = service._merge_opportunities([first, second], [])

    assert len(merged) == 2
    assert {item.icp_id for item in merged} == {first.icp_id, second.icp_id}


def test_adaptive_merge_keeps_community_and_post_separate_for_same_icp() -> None:
    service = InMemoryAudienceIntelligenceService(MemoryRuntimeStateStore())
    icp_id = uuid4()
    community = _view(icp_id=icp_id, canonical_key="telegram:42:community")
    post = _view(icp_id=icp_id, canonical_key="telegram:42:post:123")

    merged = service._merge_opportunities([community], [post])

    assert len(merged) == 2
    assert {item.canonical_key for item in merged} == {
        "telegram:42:community",
        "telegram:42:post:123",
    }


def test_same_icp_same_surface_still_merges_into_existing_identity() -> None:
    service = InMemoryAudienceIntelligenceService(MemoryRuntimeStateStore())
    icp_id = uuid4()
    current = _view(icp_id=icp_id, canonical_key="telegram:42:post:123", score=60)
    incoming = _view(icp_id=icp_id, canonical_key="telegram:42:post:123", score=90)
    incoming.evidence = [{"url": "https://t.me/example/123", "title": "fresh"}]

    merged = service._merge_opportunities([current], [incoming])

    assert len(merged) == 1
    assert merged[0].id == current.id
    assert merged[0].relevance_score == 90
    assert merged[0].evidence == incoming.evidence
