from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_schemas import DistributionActionExecutionRequest
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_service import distribution_play_service
from app.icp_service import icp_service
from app.main import app
from app.outreach_briefs import outreach_brief_service
from app.outreach_learning import outreach_learning_feed_service
from app.outreach_targets import outreach_target_service
from app.product_intake import product_intake_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    audience_intelligence_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    distribution_analytics_service.reset()
    distribution_growth_manager_service.reset()
    outreach_target_service.reset()
    outreach_brief_service.reset()
    outreach_learning_feed_service.reset()


def _running_outreach_experiment() -> tuple[str, dict]:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized reflective readings available on demand.\n"
                "Market: US\nLanguage: English\nPrice: 6.90 USD per month\n"
                "Budget: 1000\nMax CAC: 12\nGoal: Acquire paid subscribers"
            ),
            "reference_links": ["https://oracle.example/product"],
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    discovery = client.post(f"/v1/products/{product_id}/distribution/discover")
    opportunity = next(item for item in discovery.json()["opportunities"] if item.get("url"))

    target = client.post(
        f"/v1/products/{product_id}/outreach-targets",
        json={
            "opportunity_id": opportunity["id"],
            "target_type": "CREATOR",
            "canonical_name": opportunity["title"],
            "target_url": opportunity["url"],
            "business_email": "collabs@creator.example",
            "contact_evidence": {
                "provenance_type": "OPERATOR_SUPPLIED",
                "source_label": "Business address supplied by operator",
            },
            "relevance_rationale": "The creator publishes content aligned with the product use case.",
            "icp_overlap_rationale": "The audience overlaps the confirmed product ICP.",
            "confidence": 90,
            "language": "English",
            "jurisdiction": "US",
        },
    ).json()
    brief_response = client.post(f"/v1/outreach-targets/{target['id']}/briefs", json={})
    assert brief_response.status_code == 201
    brief = brief_response.json()

    approved = distribution_execution_service.approve_outreach(UUID(brief["action_id"]))
    distribution_execution_service.mark_executed(
        approved.action.id,
        DistributionActionExecutionRequest(external_reference="learning-test-send"),
    )
    return product_id, brief


def test_outreach_conversion_feeds_growth_manager_and_learning_memory_once() -> None:
    product_id, brief = _running_outreach_experiment()

    event = client.post(
        "/v1/distribution-analytics/events",
        json={
            "event_type": "PAID",
            "experiment_id": brief["experiment_id"],
            "actor_id": "paid-user-1",
            "revenue": 6.9,
        },
    )
    assert event.status_code == 201

    first = outreach_learning_feed_service.feed(UUID(product_id))
    second = outreach_learning_feed_service.feed(UUID(product_id))
    memory = distribution_growth_manager_service.learning_memory(UUID(product_id))

    assert len(first.evaluated) == 1
    assert first.evaluated[0].experiment_id == UUID(brief["experiment_id"])
    assert first.evaluated[0].growth_action == "CONTINUE"
    assert second.evaluated == []
    assert len(memory.entries) == 1
    assert memory.entries[0].experiment_id == UUID(brief["experiment_id"])
    assert memory.entries[0].paid_users == 1
    assert memory.entries[0].revenue == pytest.approx(6.9)


def test_outreach_learning_waits_for_real_attributed_signal() -> None:
    product_id, _ = _running_outreach_experiment()

    result = outreach_learning_feed_service.feed(UUID(product_id))
    memory = distribution_growth_manager_service.learning_memory(UUID(product_id))

    assert result.evaluated == []
    assert memory.entries == []


def test_controlled_growth_feeds_learning_before_attempting_next_autosend() -> None:
    javascript_order_is_irrelevant = True
    assert javascript_order_is_irrelevant

    source = open("app/autonomous_controlled_growth.py", encoding="utf-8").read()
    feed_call = "self._outreach_learning_service.feed(mandate.product_id)"
    send_call = "self._outreach_send_service.run_next(mandate.product_id)"

    assert feed_call in source
    assert send_call in source
    assert source.index(feed_call) < source.index(send_call)
