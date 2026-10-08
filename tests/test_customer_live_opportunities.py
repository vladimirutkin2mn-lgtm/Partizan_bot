from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.customer_live_opportunities import (
    CUSTOMER_PROJECT_NAMESPACE,
    MAX_TELEGRAM_OPPORTUNITY_AGE,
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


def _project_store():
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    project_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {"id": str(project_id), "product_id": str(product_id)},
    )
    return store, product_id, project_id


def _telegram_opportunity(
    *,
    target_url: str,
    published_at: datetime | None,
    checked_at: datetime | None = None,
):
    recent_context = [{"url": target_url}]
    if published_at is not None:
        recent_context[0]["published_at"] = published_at.isoformat()
    return SimpleNamespace(
        id=uuid4(),
        platform=SimpleNamespace(value="TELEGRAM"),
        kind=SimpleNamespace(value="CHANNEL"),
        title="Example community",
        relevance_score=83.5,
        rationale="Native Telegram evidence matches the target audience.",
        metadata={
            "native_research_status": "VERIFIED",
            "source_checked_at": (checked_at or datetime.now(UTC)).isoformat(),
            "telegram_entity_id": 123,
            "handle": "example",
            "linked_discussion_id": 456,
            "action_target_specific": True,
            "action_target_url": target_url,
            "recent_context": recent_context,
        },
    )


def test_live_opportunity_is_scoped_sorted_and_freshness_is_derived() -> None:
    store = MemoryRuntimeStateStore()
    service = CustomerLiveOpportunityService(store)
    project_id = uuid4()
    other_project_id = uuid4()
    now = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)

    service.upsert(project_id, _request(title="Lower score", relevance_score=50))
    service.upsert(
        project_id,
        _request(
            title="Higher score",
            url="https://t.me/example/43",
            relevance_score=95,
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

    assert (
        service.update_publishability_by_url(
            project_id,
            target,
            publishability="JOIN_REQUIRED",
            detail="Join the linked discussion group before publishing.",
        )
        == 1
    )
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
    assert second.publishability == "READY"
    assert len(service.list_for_project(project_id)) == 1


def test_native_telegram_discovery_is_promoted_to_owning_customer_projects() -> None:
    store = MemoryRuntimeStateStore()
    service = CustomerLiveOpportunityService(store)
    product_id = uuid4()
    project_id = uuid4()
    unrelated_project_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {"id": str(project_id), "product_id": str(product_id)},
    )
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(unrelated_project_id),
        {"id": str(unrelated_project_id), "product_id": str(uuid4())},
    )
    checked_at = datetime.now(UTC)
    published_at = checked_at - timedelta(hours=2)
    target_url = "https://t.me/example/777"
    opportunity = _telegram_opportunity(
        target_url=target_url,
        published_at=published_at,
        checked_at=checked_at,
    )

    assert service.sync_distribution_opportunities(product_id, [opportunity]) == 1
    items = service.list_for_project(project_id, now=checked_at)

    assert len(items) == 1
    assert str(items[0].url).rstrip("/") == target_url
    assert items[0].source == "RESEARCH"
    assert items[0].publishability == "NEEDS_REVIEW"
    assert items[0].relevance_score == 83.5
    assert items[0].expires_at == published_at + MAX_TELEGRAM_OPPORTUNITY_AGE
    assert service.list_for_project(unrelated_project_id) == []


def test_old_telegram_post_is_rejected_even_when_crawl_is_current() -> None:
    store, product_id, project_id = _project_store()
    service = CustomerLiveOpportunityService(store)
    checked_at = datetime.now(UTC)
    target_url = "https://t.me/example/old"
    opportunity = _telegram_opportunity(
        target_url=target_url,
        published_at=checked_at - MAX_TELEGRAM_OPPORTUNITY_AGE - timedelta(minutes=1),
        checked_at=checked_at,
    )

    assert service.sync_distribution_opportunities(product_id, [opportunity]) == 0
    assert service.list_for_project(project_id) == []


def test_telegram_post_without_real_published_at_is_rejected() -> None:
    store, product_id, project_id = _project_store()
    service = CustomerLiveOpportunityService(store)
    target_url = "https://t.me/example/unknown-date"
    opportunity = _telegram_opportunity(
        target_url=target_url,
        published_at=None,
        checked_at=datetime.now(UTC),
    )

    assert service.sync_distribution_opportunities(product_id, [opportunity]) == 0
    assert service.list_for_project(project_id) == []


def test_stale_or_undated_refresh_retires_existing_live_target() -> None:
    store, product_id, project_id = _project_store()
    service = CustomerLiveOpportunityService(store)
    target_url = "https://t.me/example/rediscovered"
    now = datetime.now(UTC)
    fresh = _telegram_opportunity(
        target_url=target_url,
        published_at=now - timedelta(hours=1),
        checked_at=now,
    )
    assert service.sync_distribution_opportunities(product_id, [fresh]) == 1
    assert service.list_for_project(project_id)[0].status == "ACTIVE"

    undated = _telegram_opportunity(
        target_url=target_url,
        published_at=None,
        checked_at=now + timedelta(minutes=1),
    )
    assert service.sync_distribution_opportunities(product_id, [undated]) == 0

    retired = service.list_for_project(project_id)[0]
    assert retired.status == "STALE"
    assert retired.freshness == "STALE"
    assert "published_at is unavailable" in str(retired.publishability_detail)


def test_generic_telegram_channel_without_specific_action_target_stays_out_of_live_feed() -> None:
    store = MemoryRuntimeStateStore()
    service = CustomerLiveOpportunityService(store)
    product_id = uuid4()
    project_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {"id": str(project_id), "product_id": str(product_id)},
    )
    generic = SimpleNamespace(
        platform=SimpleNamespace(value="TELEGRAM"),
        metadata={
            "action_target_specific": False,
            "action_target_url": None,
        },
    )

    assert service.sync_distribution_opportunities(product_id, [generic]) == 0
    assert service.list_for_project(project_id) == []
