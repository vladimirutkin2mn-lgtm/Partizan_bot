from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.distribution_schemas import DistributionOpportunitySeed
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.telegram_participant_research import (
    TelegramParticipantObservation,
    TelegramParticipantResearchService,
)


class FakeTransport:
    def __init__(self, observations):
        self.observations = observations
        self.calls = []

    async def observe(self, handles, *, message_limit):
        self.calls.append((list(handles), message_limit))
        return self.observations


def _icp():
    return SimpleNamespace(
        id=uuid4(),
        title="Career changers and job seekers",
        description="People looking for practical career coaching and peer support",
        pain="hard to find trusted job search guidance",
        trigger="ready to ask questions about interviews",
        desired_outcome="find trusted people and practical career advice",
        alternatives=["career communities", "job search groups"],
    )


def _opportunity(icp_id, *, handle="example", score=80.0, platform=DistributionPlatform.TELEGRAM):
    return DistributionOpportunitySeed(
        icp_id=icp_id,
        platform=platform,
        kind=(
            OpportunityKind.GROUP
            if platform == DistributionPlatform.TELEGRAM
            else OpportunityKind.CREATOR_ACCOUNT
        ),
        canonical_key=f"{str(platform.value).lower()}:{handle}",
        title=handle,
        url=(f"https://t.me/{handle}" if platform == DistributionPlatform.TELEGRAM else None),
        relevance_score=score,
        rationale="research candidate",
        metadata={"handle": handle} if platform == DistributionPlatform.TELEGRAM else {},
        evidence=[],
    )


@pytest.mark.asyncio
async def test_enrich_persists_only_aggregate_participant_counts_not_identities() -> None:
    icp = _icp()
    now = datetime.now(UTC)
    transport = FakeTransport(
        {
            "example": (
                TelegramParticipantObservation(101, "AUTHOR", "Career interview advice", now),
                TelegramParticipantObservation(101, "AUTHOR", "Job search interview question", now),
                TelegramParticipantObservation(202, "AUTHOR", "Trusted career community", now),
                TelegramParticipantObservation(303, "AUTHOR", "Completely unrelated sports", now),
                TelegramParticipantObservation(404, "AUTHOR", "Career bot spam", now, is_bot=True),
                TelegramParticipantObservation(505, "COMMENTER", "Practical job search advice", now),
            )
        }
    )
    service = TelegramParticipantResearchService(
        transport,
        use_default_transport=False,
        max_communities=4,
        message_limit=8,
    )

    enriched = await service.enrich([_opportunity(icp.id)], [icp])
    evidence = enriched[0].metadata["participant_evidence"]

    assert evidence["observed_message_count"] == 5
    assert evidence["distinct_participant_count"] == 4
    assert evidence["distinct_author_count"] == 3
    assert evidence["distinct_commenter_count"] == 1
    assert evidence["matched_participant_count"] == 3
    assert evidence["matched_author_count"] == 2
    assert evidence["matched_commenter_count"] == 1
    assert evidence["repeat_participant_count"] == 1
    assert evidence["identity_persisted"] is False
    assert evidence["participant_ids_persisted"] is False
    assert evidence["usernames_persisted"] is False
    persisted_integer_values = {
        value
        for value in evidence.values()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    assert not ({101, 202, 303, 404, 505} & persisted_integer_values)


@pytest.mark.asyncio
async def test_participant_research_is_bounded_and_dedupes_same_handle_across_icps() -> None:
    first_icp = _icp()
    second_icp = _icp()
    transport = FakeTransport({})
    service = TelegramParticipantResearchService(
        transport,
        use_default_transport=False,
        max_communities=2,
        message_limit=6,
    )
    opportunities = [
        _opportunity(first_icp.id, handle="same", score=99),
        _opportunity(second_icp.id, handle="same", score=98),
        _opportunity(first_icp.id, handle="second", score=97),
        _opportunity(first_icp.id, handle="third", score=96),
        _opportunity(
            first_icp.id,
            handle="instagram",
            score=100,
            platform=DistributionPlatform.INSTAGRAM,
        ),
    ]

    await service.enrich(opportunities, [first_icp, second_icp])

    assert transport.calls == [(["same", "second"], 6)]


@pytest.mark.asyncio
async def test_transport_failure_never_discards_existing_discovery_candidates() -> None:
    class BrokenTransport:
        async def observe(self, handles, *, message_limit):
            del handles, message_limit
            raise RuntimeError("provider unavailable")

    icp = _icp()
    opportunity = _opportunity(icp.id)
    service = TelegramParticipantResearchService(
        BrokenTransport(),
        use_default_transport=False,
    )

    result = await service.enrich([opportunity], [icp])

    assert len(result) == 1
    assert result[0].canonical_key == opportunity.canonical_key
    assert "participant_evidence" not in result[0].metadata
