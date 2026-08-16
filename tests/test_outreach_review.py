from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.icp_service import icp_service
from app.main import app
from app.outreach_briefs import outreach_brief_service
from app.outreach_sender import outreach_sender_service
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
    outreach_target_service.reset()
    outreach_brief_service.reset()
    outreach_sender_service.reset()


def _draft() -> dict:
    product = client.post(
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
    ).json()["product"]
    product_id = product["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    discovery = client.post(f"/v1/products/{product_id}/distribution/discover").json()
    opportunity = next(item for item in discovery["opportunities"] if item.get("url"))

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
            "icp_overlap_rationale": "The discovered audience overlaps the confirmed product ICP.",
            "confidence": 84,
            "language": "English",
            "jurisdiction": "US",
        },
    ).json()
    response = client.post(f"/v1/outreach-targets/{target['id']}/briefs", json={})
    assert response.status_code == 201
    return response.json()


def test_review_edit_preserves_single_tracking_url_and_exact_action_content() -> None:
    brief = _draft()
    tracking_url = brief["tracking_url"]

    response = client.patch(
        f"/v1/outreach-briefs/{brief['id']}/review",
        json={
            "message_subject": "A smaller collaboration idea",
            "message_body_without_link": (
                "Hi there,\n\nWe think a small creator test could be relevant to your audience. "
                "If it is interesting, we can start with one attributable experiment.\n\nBest,\nPartizan"
            ),
        },
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["message_subject"] == "A smaller collaboration idea"
    assert updated["tracking_url"] == tracking_url
    assert updated["message_body"].count(tracking_url) == 1
    assert updated["message_body"].endswith(f"Product details: {tracking_url}")

    action = distribution_execution_service.get_action(UUID(updated["action_id"]))
    assert action.status.value == "PREPARED"
    assert action.content_text is not None
    assert action.content_text.startswith("Subject: A smaller collaboration idea")
    assert action.content_text.count(tracking_url) == 1


def test_review_edit_rejects_untracked_urls() -> None:
    brief = _draft()

    response = client.patch(
        f"/v1/outreach-briefs/{brief['id']}/review",
        json={
            "message_subject": "Collaboration idea",
            "message_body_without_link": (
                "Hi there, please review this collaboration at https://untracked.example "
                "and let us know if it looks relevant to your audience."
            ),
        },
    )

    assert response.status_code == 422
    current = outreach_brief_service.get(UUID(brief["id"]))
    assert current.message_subject == brief["message_subject"]
    assert current.message_body == brief["message_body"]


def test_reject_cancels_draft_without_external_execution() -> None:
    brief = _draft()

    response = client.post(f"/v1/outreach-briefs/{brief['id']}/reject")

    assert response.status_code == 200
    rejected = response.json()
    assert rejected["status"] == "REJECTED"
    action = distribution_execution_service.get_action(UUID(brief["action_id"]))
    experiment = distribution_execution_service.get_experiment(UUID(brief["experiment_id"]))
    assert action.status.value == "SKIPPED"
    assert experiment.status.value == "CANCELLED"
    assert outreach_sender_service.get_attempt(UUID(brief["id"])) is None

    second_edit = client.patch(
        f"/v1/outreach-briefs/{brief['id']}/review",
        json={
            "message_subject": "Should not save",
            "message_body_without_link": (
                "This draft has already been rejected and must not become editable again later."
            ),
        },
    )
    assert second_edit.status_code == 409
