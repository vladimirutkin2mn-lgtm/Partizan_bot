import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_play_service import distribution_play_service
from app.icp_service import icp_service
from app.main import app
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
    outreach_target_service.reset()


def _product_and_opportunities() -> tuple[str, list[dict]]:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized readings available on demand.\n"
                "Market: US\n"
                "Language: English\n"
                "Budget: 200\n"
                "Goal: Acquire 100 paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    discovery = client.post(f"/v1/products/{product_id}/distribution/discover")
    assert discovery.status_code == 200
    with_urls = [item for item in discovery.json()["opportunities"] if item.get("url")]
    assert len(with_urls) >= 2
    return product_id, with_urls


def _operator_target(opportunity: dict, email: str) -> dict:
    return {
        "opportunity_id": opportunity["id"],
        "target_type": "PARTNER",
        "canonical_name": opportunity["title"],
        "target_url": opportunity["url"],
        "business_email": email,
        "contact_evidence": {
            "provenance_type": "OPERATOR_SUPPLIED",
            "source_label": "Provided directly by operator",
        },
        "relevance_rationale": (
            "Concrete distribution opportunity already discovered by Partizan."
        ),
        "icp_overlap_rationale": "The opportunity was discovered for the confirmed product ICP.",
        "confidence": 75,
    }


def test_suppression_follows_contact_across_opportunities() -> None:
    product_id, opportunities = _product_and_opportunities()
    email = "partnerships@creator.example"

    first = client.post(
        f"/v1/products/{product_id}/outreach-targets",
        json=_operator_target(opportunities[0], email),
    )
    assert first.status_code == 201
    assert first.json()["executable"] is True

    suppressed = client.post(
        f"/v1/outreach-targets/{first.json()['id']}/suppress",
        json={"reason": "OPT_OUT", "note": "Do not contact again."},
    )
    assert suppressed.status_code == 200
    assert suppressed.json()["executable"] is False

    reintroduced = client.post(
        f"/v1/products/{product_id}/outreach-targets",
        json=_operator_target(opportunities[1], email.upper()),
    )
    assert reintroduced.status_code == 409
    assert "suppressed" in reintroduced.json()["detail"]
    assert "OPT_OUT" in reintroduced.json()["detail"]
