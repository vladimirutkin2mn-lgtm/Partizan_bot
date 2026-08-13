from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.channel_service import channel_service
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.main import app
from app.outreach_briefs import outreach_brief_service
from app.outreach_targets import outreach_target_service
from app.product_intake import product_intake_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    channel_service.reset()
    audience_intelligence_service.reset()
    growth_play_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    distribution_analytics_service.reset()
    outreach_target_service.reset()
    outreach_brief_service.reset()


def _product_and_target() -> tuple[str, dict]:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized reflective readings available on demand.\n"
                "Market: US\n"
                "Language: English\n"
                "Price: 6.90 USD per month\n"
                "Budget: 1000\n"
                "Max CAC: 12\n"
                "Goal: Acquire paid subscribers"
            ),
            "reference_links": ["https://oracle.example/product"],
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    discovery = client.post(f"/v1/products/{product_id}/distribution/discover")
    assert discovery.status_code == 200
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
            "icp_overlap_rationale": "The discovered audience overlaps the confirmed product ICP.",
            "confidence": 84,
            "language": "English",
            "jurisdiction": "US",
        },
    )
    assert target.status_code == 201
    return product_id, target.json()


def _brief(target_id: str, offer: str = "CREATOR_SEEDING") -> dict:
    response = client.post(
        f"/v1/outreach-targets/{target_id}/briefs",
        json={"preferred_offer_type": offer},
    )
    assert response.status_code == 201
    return response.json()


def test_outreach_brief_creates_traceable_distribution_experiment() -> None:
    product_id, target = _product_and_target()
    brief = _brief(target["id"])

    assert brief["product_id"] == product_id
    assert brief["outreach_target_id"] == target["id"]
    assert brief["offer_type"] == "CREATOR_SEEDING"
    assert brief["follow_up_policy"] == "ONE_INITIAL_MESSAGE_NO_AUTONOMOUS_FOLLOWUP"
    assert brief["tracking_url"] in brief["message_body"]
    assert brief["message_body"].count(brief["tracking_url"]) == 1
    assert "fabricated prior relationship" in " ".join(brief["prohibited_claims"])

    action = distribution_execution_service.get_action(UUID(brief["action_id"]))
    experiment = distribution_execution_service.get_experiment(UUID(brief["experiment_id"]))
    play = distribution_play_service.find(UUID(product_id), UUID(brief["distribution_play_id"]))

    assert action.action_type.value == "OUTREACH_EMAIL"
    assert action.status.value == "PREPARED"
    assert experiment.status.value == "DRAFT"
    assert experiment.action_id == action.id
    assert experiment.distribution_play_id == play.id
    assert play.tactic_class.value == "OUTREACH"
    assert play.opportunity_id == UUID(target["opportunity_id"])
    assert action.tracking_url is not None
    assert str(action.tracking_url) == brief["tracking_url"]
    assert action.content_text is not None
    assert action.content_text.startswith(f"Subject: {brief['message_subject']}")
    assert brief["tracking_url"] in action.content_text

    analytics = distribution_analytics_service.experiment_analytics(experiment.id)
    assert analytics.experiment.id == experiment.id
    assert analytics.play.id == play.id
    assert analytics.action.id == action.id
    assert analytics.metrics.spend == 0


def test_only_one_active_draft_is_allowed_per_target() -> None:
    product_id, target = _product_and_target()
    path = f"/v1/outreach-targets/{target['id']}/briefs"

    first = client.post(path, json={"preferred_offer_type": "CROSS_PROMO"})
    different_offer = client.post(path, json={"preferred_offer_type": "AFFILIATE"})

    assert first.status_code == 201
    assert different_offer.status_code == 409
    assert "already exists" in different_offer.json()["detail"]
    experiments = distribution_execution_service.list_experiments(UUID(product_id))
    assert len(experiments) == 1


def test_suppressed_target_cannot_prepare_outreach_experiment() -> None:
    product_id, target = _product_and_target()
    suppressed = client.post(
        f"/v1/outreach-targets/{target['id']}/suppress",
        json={"reason": "OPT_OUT", "note": "Recipient requested no further contact."},
    )
    assert suppressed.status_code == 200

    response = client.post(
        f"/v1/outreach-targets/{target['id']}/briefs",
        json={},
    )

    assert response.status_code == 409
    assert "OPT_OUT" in response.json()["detail"]
    assert distribution_execution_service.list_experiments(UUID(product_id)) == []


def test_generated_or_operator_body_cannot_inject_an_untracked_url() -> None:
    product_id, target = _product_and_target()

    response = client.post(
        f"/v1/outreach-targets/{target['id']}/briefs",
        json={
            "operator_offer_context": (
                "review the collaboration terms at https://untracked.example before replying"
            )
        },
    )

    assert response.status_code == 409
    assert "must not contain URLs" in response.json()["detail"]
    assert distribution_execution_service.list_experiments(UUID(product_id)) == []


def test_registered_outreach_play_survives_channel_play_regeneration() -> None:
    product_id, target = _product_and_target()
    brief = _brief(target["id"])
    outreach_play_id = UUID(brief["distribution_play_id"])
    product_uuid = UUID(product_id)

    product = product_intake_service.get_product(product_uuid)
    distribution_map = audience_intelligence_service.get(product_uuid)
    regenerated = distribution_play_service.generate(product, distribution_map)

    persisted = next(item for item in regenerated.plays if item.id == outreach_play_id)
    assert persisted.tactic_class.value == "OUTREACH"
    assert distribution_play_service.find(product_uuid, outreach_play_id).id == outreach_play_id


def test_generic_distribution_mutations_cannot_bypass_outreach_flow() -> None:
    product_id, target = _product_and_target()
    brief = _brief(target["id"])
    action_id = brief["action_id"]
    play_id = brief["distribution_play_id"]

    prepare = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play_id}/actions/prepare",
        json={"destination_url": "https://oracle.example/product"},
    )
    auto_prepare = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play_id}/actions/auto-prepare",
        json={"destination_url": "https://oracle.example/product"},
    )
    approve = client.post(f"/v1/distribution-actions/{action_id}/approve")
    execute = client.post(f"/v1/distribution-actions/{action_id}/execute", json={})
    mark_executed = client.post(
        f"/v1/distribution-actions/{action_id}/mark-executed",
        json={"external_reference": "fake-send"},
    )

    for response in (prepare, auto_prepare, approve, execute, mark_executed):
        assert response.status_code == 409
        assert "outreach" in response.json()["detail"].lower()
    plan = distribution_execution_service.get_plan(UUID(action_id))
    assert plan.action.status.value == "PREPARED"
    assert plan.experiment.status.value == "DRAFT"
    assert len(distribution_execution_service.list_experiments(UUID(product_id))) == 1


def test_outreach_brief_uses_only_actual_product_facts() -> None:
    _, target = _product_and_target()

    response = client.post(
        f"/v1/outreach-targets/{target['id']}/briefs",
        json={"operator_offer_context": "one small cross-promotion test with referral attribution"},
    )

    assert response.status_code == 201
    brief = response.json()
    facts = "\n".join(brief["allowed_product_facts"])
    assert "Oracle" in facts
    assert "Personalized reflective readings available on demand" in facts
    body = brief["message_body"].lower()
    for fabricated_phrase in (
        "millions of users",
        "market leader",
        "as we discussed",
        "guaranteed conversion",
    ):
        assert fabricated_phrase not in body
