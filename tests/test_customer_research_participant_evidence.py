from datetime import UTC, datetime
from uuid import uuid4

from app.customer_research_communities import (
    AUDIENCE_MAP_NAMESPACE,
    CUSTOMER_PROJECT_NAMESPACE,
    CustomerResearchCommunityService,
)
from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.runtime_store import MemoryRuntimeStateStore


def _seed(*, identity_persisted: bool = False):
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    product_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {"id": str(project_id), "product_id": str(product_id)},
    )
    opportunity = DistributionOpportunityView(
        id=uuid4(),
        icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key="telegram:700:community",
        title="Career community",
        url="https://t.me/careercommunity",
        relevance_score=82.0,
        rationale="Observed audience evidence supports this research lead.",
        metadata={
            "opportunity_scope": "COMMUNITY",
            "actionability": "RESEARCH_ONLY",
            "handle": "careercommunity",
            "member_count": 2500,
            "source_checked_at": "2026-10-13T18:00:00+00:00",
            "participant_evidence": {
                "source_checked_at": "2026-10-13T18:05:00+00:00",
                "observed_message_count": 7,
                "matched_message_count": 4,
                "distinct_participant_count": 5,
                "matched_participant_count": 3,
                "repeat_participant_count": 1,
                "matched_participant_ratio": 0.6,
                "identity_persisted": identity_persisted,
                "participant_ids_persisted": identity_persisted,
                "usernames_persisted": identity_persisted,
                # Deliberately simulate unsafe future source data. The customer view must
                # fail closed and never surface these fields.
                "actor_id": 123456,
                "participant_ids": [123456, 999999],
                "usernames": ["private_person"],
            },
        },
    )
    audience_map = AudienceDistributionMapView(
        product_id=product_id,
        top_icp_count=1,
        opportunity_count=1,
        opportunities=[opportunity],
    )
    store.put(
        AUDIENCE_MAP_NAMESPACE,
        str(product_id),
        audience_map.model_dump(mode="json"),
    )
    return store, project_id


def test_customer_view_exposes_only_aggregate_participant_evidence() -> None:
    store, project_id = _seed(identity_persisted=False)

    item = CustomerResearchCommunityService(store).list_for_project(project_id)[0]
    payload = item.model_dump(mode="json")

    assert item.observed_participant_count == 5
    assert item.matched_participant_count == 3
    assert item.repeat_participant_count == 1
    assert item.observed_message_count == 7
    assert item.matched_message_count == 4
    assert item.participant_match_ratio == 0.6
    assert item.participant_evidence_checked_at == datetime(
        2026, 10, 13, 18, 5, tzinfo=UTC
    )
    assert "actor_id" not in payload
    assert "participant_ids" not in payload
    assert "usernames" not in payload
    assert "identity_persisted" not in payload


def test_customer_view_fails_closed_if_source_starts_persisting_identity() -> None:
    store, project_id = _seed(identity_persisted=True)

    item = CustomerResearchCommunityService(store).list_for_project(project_id)[0]

    assert item.observed_participant_count is None
    assert item.matched_participant_count is None
    assert item.repeat_participant_count is None
    assert item.observed_message_count is None
    assert item.matched_message_count is None
    assert item.participant_match_ratio is None
    assert item.participant_evidence_checked_at is None
