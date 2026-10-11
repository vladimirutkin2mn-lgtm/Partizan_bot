from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.customer_live_opportunities import (
    CUSTOMER_PROJECT_NAMESPACE,
    CustomerLiveOpportunityService,
    CustomerLiveOpportunityUpsert,
)
from app.manual_live_opportunity_report import ManualLiveOpportunityReportService
from app.runtime_store import MemoryRuntimeStateStore


def test_report_is_read_only_and_filters_current_research_telegram_rows() -> None:
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    product_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {"id": str(project_id), "product_id": str(product_id)},
    )
    opportunities = CustomerLiveOpportunityService(store)
    now = datetime.now(UTC)
    opportunities.upsert(
        project_id,
        CustomerLiveOpportunityUpsert(
            source="RESEARCH",
            platform="TELEGRAM",
            kind="COMMENT",
            title="Relevant current target",
            url="https://t.me/example/123",
            rationale="Observed current audience evidence.",
            recommended_action="Review only.",
            relevance_score=55.0,
            publishability="JOIN_REQUIRED",
            discovered_at=now,
            expires_at=now + timedelta(days=2),
        ),
    )
    opportunities.upsert(
        project_id,
        CustomerLiveOpportunityUpsert(
            source="OPERATIONAL_DISCOVERY",
            platform="TELEGRAM",
            kind="COMMENT",
            title="Reviewed seed",
            url="https://t.me/example/456",
            rationale="Reviewed seed target.",
            recommended_action="Review only.",
            relevance_score=90.0,
            publishability="NEEDS_REVIEW",
            discovered_at=now,
            expires_at=now + timedelta(days=2),
        ),
    )
    before = store.list_namespace("customer_live_opportunities")

    report = ManualLiveOpportunityReportService(
        store=store,
        opportunity_service=opportunities,
    ).build(
        project_id=project_id,
        expected_product_id=product_id,
        platform="TELEGRAM",
        source="RESEARCH",
        limit=10,
    )

    after = store.list_namespace("customer_live_opportunities")
    assert before == after
    assert report["status"] == "READ_ONLY"
    assert report["active_fresh_count"] == 1
    assert report["publishability"] == {"JOIN_REQUIRED": 1}
    assert report["sources"] == {"RESEARCH": 1}
    assert report["score"] == {
        "count": 1,
        "average": 55.0,
        "min": 55.0,
        "max": 55.0,
        "ge_70": 0,
    }
    assert report["opportunities"][0]["title"] == "Relevant current target"


def test_report_rejects_project_product_mismatch() -> None:
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {"id": str(project_id), "product_id": str(uuid4())},
    )

    with pytest.raises(ValueError, match="product mismatch"):
        ManualLiveOpportunityReportService(store=store).build(
            project_id=project_id,
            expected_product_id=uuid4(),
        )
