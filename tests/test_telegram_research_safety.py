import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.audience_intelligence import AudienceIntelligenceEngine
from app.config import Settings
from app.platform_discovery import TelegramDiscoveryAdapter
from app.search import DiscoveryQuery, SearchHit, SearchProvider
from app.telegram_research import (
    TelegramCommunitySnapshot,
    TelegramResearchConnector,
    TelegramSurfaceKind,
    TelethonTelegramResearchTransport,
)


class RecordingTransport:
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


class EmptySearchProvider(SearchProvider):
    async def search(
        self,
        discovery_query: DiscoveryQuery,
        limit: int = 5,
    ) -> list[SearchHit]:
        del discovery_query, limit
        return []


def _community_snapshot(entity_id: int) -> TelegramCommunitySnapshot:
    return TelegramCommunitySnapshot(
        entity_id=entity_id,
        username=f"community_{entity_id}",
        title=f"Community {entity_id}",
        kind=TelegramSurfaceKind.GROUP,
        url=f"https://t.me/community_{entity_id}",
        source_checked_at=datetime(2026, 9, 8, 8, 0, tzinfo=UTC),
        last_activity_at=datetime(2026, 9, 8, 7, 0, tzinfo=UTC) + timedelta(minutes=entity_id),
    )


def _telethon_snapshot(
    transport: TelethonTelegramResearchTransport,
    *,
    published_at: datetime,
    text: str = "Founders are comparing bookkeeping automation tools",
) -> TelegramCommunitySnapshot:
    entity = SimpleNamespace(
        id=9001,
        username="founder_books",
        title="Founder Bookkeeping",
        megagroup=True,
        broadcast=False,
        participants_count=4200,
    )
    full = SimpleNamespace(
        full_chat=SimpleNamespace(
            linked_chat_id=None,
            about="A public group for founders discussing bookkeeping tools.",
            participants_count=4200,
        )
    )
    message = SimpleNamespace(id=77, message=text, date=published_at)
    return transport._snapshot(
        entity,
        full,
        [message],
        query="founders bookkeeping automation",
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
async def test_connector_hard_caps_query_handles_results_and_recent_context_limits() -> None:
    transport = RecordingTransport([_community_snapshot(index) for index in range(1, 21)])
    connector = TelegramResearchConnector(
        transport,
        result_limit=999,
        known_handle_limit=999,
        recent_message_limit=999,
        query_length_limit=999,
    )

    result = await connector.discover(
        query="x" * 500,
        known_handles=[f"handle_{index}" for index in range(20)],
    )

    assert transport.calls == [
        {
            "query": "x" * 160,
            "known_handles": [f"handle_{index}" for index in range(5)],
            "result_limit": 10,
            "recent_message_limit": 8,
        }
    ]
    assert len(result) == 15


def test_specific_action_target_requires_fresh_matching_message_context() -> None:
    transport = TelethonTelegramResearchTransport(
        api_id=12345,
        api_hash="api-hash-secret",
        session_string="session-secret",
    )

    stale = _telethon_snapshot(
        transport,
        published_at=datetime.now(UTC) - timedelta(days=15),
    )
    fresh = _telethon_snapshot(
        transport,
        published_at=datetime.now(UTC) - timedelta(days=1),
    )
    fresh_but_irrelevant = _telethon_snapshot(
        transport,
        published_at=datetime.now(UTC) - timedelta(hours=1),
        text="General community announcement without the researched topic",
    )

    assert stale.recent_context[0].matched_terms
    assert stale.action_target_url is None
    assert fresh.action_target_url == "https://t.me/founder_books/77"
    assert fresh_but_irrelevant.recent_context[0].matched_terms == ()
    assert fresh_but_irrelevant.action_target_url is None


@pytest.mark.asyncio
async def test_telegram_research_secrets_never_enter_serialized_opportunity_payload() -> None:
    api_hash = "sentinel-api-hash-do-not-leak"
    session = "sentinel-session-do-not-leak"
    settings = Settings(
        telegram_research_provider="telethon",
        telegram_research_public_ready=True,
        telegram_research_api_id=12345,
        telegram_research_api_hash=api_hash,
        telegram_research_session=session,
    )
    transport = TelethonTelegramResearchTransport(
        api_id=12345,
        api_hash=api_hash,
        session_string=session,
    )
    snapshot = _telethon_snapshot(
        transport,
        published_at=datetime.now(UTC) - timedelta(hours=1),
    )
    connector = TelegramResearchConnector(RecordingTransport([snapshot]))
    adapter = TelegramDiscoveryAdapter(
        research_connector=connector,
        use_default_research_connector=False,
    )
    engine = AudienceIntelligenceEngine(EmptySearchProvider(), adapters=[adapter])
    product, icp = _product_and_icp()

    opportunities = await engine.discover(product, [icp])

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    serialized_opportunity = json.dumps(
        {
            "metadata": opportunity.metadata,
            "evidence": opportunity.evidence,
            "snapshot": asdict(snapshot),
        },
        default=str,
    )
    serialized_settings = settings.model_dump_json()
    for secret in (api_hash, session):
        assert secret not in serialized_opportunity
        assert secret not in serialized_settings


def test_research_boundary_exposes_no_publish_or_participant_enumeration_methods() -> None:
    connector = TelegramResearchConnector(RecordingTransport([]))
    forbidden_methods = {
        "send",
        "send_message",
        "join",
        "join_channel",
        "invite",
        "invite_to_channel",
        "get_participants",
        "iter_participants",
    }

    assert forbidden_methods.isdisjoint(dir(connector))
