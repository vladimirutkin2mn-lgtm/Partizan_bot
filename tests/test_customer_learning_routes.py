from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.channel_execution import PublisherMode
from app.distribution_analytics_schemas import (
    DistributionLearningEntryView,
    DistributionLearningMemoryView,
)
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.main import app

client = TestClient(app)


def test_customer_distribution_learning_is_project_scoped_and_safe(monkeypatch) -> None:
    project_id = uuid4()
    product_id = uuid4()
    experiment_id = uuid4()
    opportunity_id = uuid4()

    monkeypatch.setattr(
        "app.customer_learning_routes.customer_account_service.project_access",
        lambda **_: (SimpleNamespace(), "customer-token"),
    )
    monkeypatch.setattr(
        "app.customer_learning_routes.customer_funnel_service.get_project_payload",
        lambda *_: {"product_id": str(product_id)},
    )
    monkeypatch.setattr(
        "app.customer_learning_routes.distribution_growth_manager_service.learning_memory",
        lambda _: DistributionLearningMemoryView(
            product_id=product_id,
            entries=[
                DistributionLearningEntryView(
                    id=uuid4(),
                    product_id=product_id,
                    experiment_id=experiment_id,
                    platform=DistributionPlatform.REDDIT,
                    tactic_id="community_reply",
                    opportunity_id=opportunity_id,
                    distribution_identity_id=uuid4(),
                    publisher_mode=PublisherMode.CLIENT_OWNED,
                    action_type=DistributionActionType.COMMENT_REPLY,
                    action="STOP",
                    observed_cac=24.5,
                    paid_users=2,
                    revenue=120.0,
                    replies=3,
                    removals=1,
                    summary="internal summary must not be returned",
                    created_at=datetime.now(UTC),
                )
            ],
        ),
    )
    monkeypatch.setattr(
        "app.customer_learning_routes.audience_intelligence_service.find_opportunity",
        lambda _: SimpleNamespace(
            title="r/startups discussion",
            url="https://www.reddit.com/r/startups/",
        ),
    )

    response = client.get(f"/customer/workspace/{project_id}/distribution-learning")

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_id"] == str(project_id)
    assert payload["product_id"] == str(product_id)
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    assert entry["experiment_id"] == str(experiment_id)
    assert entry["platform"] == "REDDIT"
    assert entry["opportunity_title"] == "r/startups discussion"
    assert entry["publisher_mode"] == "CLIENT_OWNED"
    assert entry["action_type"] == "COMMENT_REPLY"
    assert entry["decision"] == "STOP"
    assert entry["replies"] == 3
    assert entry["removals"] == 1
    assert entry["observed_cac"] == 24.5
    assert any("removal" in item for item in entry["observed_basis"])
    assert any("reply" in item for item in entry["observed_basis"])

    serialized = response.text
    assert "distribution_identity_id" not in serialized
    assert "tactic_id" not in serialized
    assert "internal summary" not in serialized
    assert "operational_cost" not in serialized


def test_customer_distribution_learning_is_empty_before_product_confirmation(monkeypatch) -> None:
    project_id = uuid4()
    monkeypatch.setattr(
        "app.customer_learning_routes.customer_account_service.project_access",
        lambda **_: (SimpleNamespace(), "customer-token"),
    )
    monkeypatch.setattr(
        "app.customer_learning_routes.customer_funnel_service.get_project_payload",
        lambda *_: {"product_id": None},
    )

    response = client.get(f"/customer/workspace/{project_id}/distribution-learning")

    assert response.status_code == 200
    assert response.json() == {
        "project_id": str(project_id),
        "product_id": None,
        "entries": [],
    }
