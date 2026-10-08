from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.customer_live_opportunities import (
    CustomerLiveOpportunityService,
    CustomerLiveOpportunityUpsert,
)
from app.runtime_store import MemoryRuntimeStateStore


def _request(**overrides) -> CustomerLiveOpportunityUpsert:
    payload = {
        "platform": "TELEGRAM",
        "surface": "COMMUNITY",
        "kind": "COMMENT",
        "title": "Relevant Telegram post",
        "url": "https://t.me/example/42",
        "rationale": "The audience matches the product and the post is current.",
        "recommended_action": "Review the comment and preflight write access.",
        "suggested_content": "A useful native comment.",
        "relevance_score": 0.9,
        "publishability": "NEEDS_REVIEW",
        "discovered_at": "2026-10-08T12:00:00+00:00",
        "expires_at": "2026-10-12T12:00:00+00:00",
    }
    payload.update(overrides)
    return CustomerLiveOpportunityUpsert.model_validate(payload)


def test_live_opportunity_is_scoped_sorted_and_freshness_is_derived() -> None:
    store = MemoryRuntimeStateStore()
    service = CustomerLiveOpportunityService(store)
    project_id = uuid4()
    other_project_id = uuid4()
    now = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)

    service.upsert(project_id, _request(title="Lower score", relevance_score=0.5))
    service.upsert(
        project_id,
        _request(
            title="Higher score",
            url="https://t.me/example/43",
            relevance_score=0.95,
        ),
    )
    service.upsert(other_project_id, _request(url="https://t.me/example/99"))

    items = service.list_for_project(project_id, now=now)

    assert [item.title for item in items] == ["Higher score", "Lower score"]
    assert all(item.freshness == "NEW" for item in items)
    assert all(str(item.url).startswith("https://t.me/example/") for item in items)


def test_expiring_and_stale_freshness_use_expiry_time() -> None:
    store = MemoryRuntimeStateStore()
    service = CustomerLiveOpportunityService(store)
    project_id = uuid4()
    now = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)

    service.upsert(
        project_id,
        _request(
            url="https://t.me/example/expiring",
            expires_at=now + timedelta(hours=24),
        ),
    )
    service.upsert(
        project_id,
        _request(
            url="https://t.me/example/stale",
            expires_at=now - timedelta(minutes=1),
        ),
    )

    items = service.list_for_project(project_id, now=now)
    by_title = {str(item.url).split("/")[-1]: item for item in items}

    assert by_title["expiring"].freshness == "EXPIRING_SOON"
    assert by_title["stale"].freshness == "STALE"


def test_publishability_and_publish_status_update_by_exact_target_url() -> None:
    store = MemoryRuntimeStateStore()
    service = CustomerLiveOpportunityService(store)
    project_id = uuid4()
    target = "https://t.me/example/42"
    service.upsert(project_id, _request(url=target))

    assert service.update_publishability_by_url(
        project_id,
        target,
        publishability="JOIN_REQUIRED",
        detail="Join the linked discussion group before publishing.",
    ) == 1
    item = service.list_for_project(project_id)[0]
    assert item.publishability == "JOIN_REQUIRED"
    assert "linked discussion" in str(item.publishability_detail)

    assert service.mark_published_by_url(project_id, target) == 1
    published = service.list_for_project(project_id)[0]
    assert published.status == "PUBLISHED"
    assert published.publishability == "READY"


def test_upsert_uses_stable_url_identity_and_preserves_published_state() -> None:
    store = MemoryRuntimeStateStore()
    service = CustomerLiveOpportunityService(store)
    project_id = uuid4()
    target = "https://t.me/example/42"

    first = service.upsert(project_id, _request(url=target))
    service.mark_published_by_url(project_id, target)
    second = service.upsert(
        project_id,
        _request(
            url=target,
            title="Updated title",
            publishability="NEEDS_REVIEW",
            status="ACTIVE",
        ),
    )

    assert first.opportunity_id == second.opportunity_id
    assert second.title == "Updated title"
    assert second.status == "PUBLISHED"
    assert len(service.list_for_project(project_id)) == 1
