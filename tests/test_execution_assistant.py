import pytest
from fastapi.testclient import TestClient

from app.channel_service import channel_service
from app.execution import ContactExtractor
from app.execution_service import execution_service
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.main import app
from app.product_intake import product_intake_service
from app.schemas import ChannelEvidenceView, ChannelOpportunityView

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    channel_service.reset()
    growth_play_service.reset()
    execution_service.reset()


def _build_growth_plays() -> tuple[str, dict]:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized readings available on demand.\n"
                "Market: US\n"
                "Goal: Acquire 100 paid users\n"
                "Budget: 500\n"
                "Max CAC: 5"
            ),
            "reference_links": ["https://example.com/oracle"],
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/channels/discover").status_code == 200
    plays = client.post(f"/v1/products/{product_id}/growth-plays/generate")
    assert plays.status_code == 200
    return product_id, plays.json()


def _approve_play(product_id: str, play: dict) -> None:
    response = client.post(
        f"/v1/products/{product_id}/growth-plays/{play['id']}/approval",
        json={"status": "APPROVED"},
    )
    assert response.status_code == 200


def test_execution_prepare_requires_approved_growth_play() -> None:
    product_id, result = _build_growth_plays()
    play = result["plays"][0]
    response = client.post(
        f"/v1/products/{product_id}/growth-plays/{play['id']}/execution/prepare",
        json={"contact_email": "partner@example.com"},
    )
    assert response.status_code == 409


def test_execution_package_can_be_edited_approved_and_run_once() -> None:
    product_id, result = _build_growth_plays()
    play = result["plays"][0]
    _approve_play(product_id, play)

    prepared = client.post(
        f"/v1/products/{product_id}/growth-plays/{play['id']}/execution/prepare",
        json={"contact_email": "partner@example.com", "contact_name": "Alex"},
    )
    assert prepared.status_code == 200
    package = prepared.json()
    package_id = package["id"]
    assert package["status"] == "PREPARED"
    assert package["contact"]["source"] == "user_override"
    assert "utm_source=" in package["tracking_url"]
    assert "ref=partizan_" in package["tracking_url"]
    assert "Alex" in package["body"]

    blocked_run = client.post(f"/v1/execution-packages/{package_id}/run")
    assert blocked_run.status_code == 409

    edited = client.patch(
        f"/v1/execution-packages/{package_id}",
        json={
            "subject": "Small partnership test",
            "body": "Hi Alex, this is an edited one-to-one partnership message for the test.",
        },
    )
    assert edited.status_code == 200
    assert edited.json()["subject"] == "Small partnership test"

    approved = client.post(f"/v1/execution-packages/{package_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"

    run = client.post(f"/v1/execution-packages/{package_id}/run")
    assert run.status_code == 200
    body = run.json()
    assert body["package"]["status"] == "SENT"
    assert body["package"]["delivery_id"].startswith("mock-")
    assert body["experiment"]["status"] == "RUNNING"
    assert body["experiment"]["delivery_id"] == body["package"]["delivery_id"]

    repeated = client.post(f"/v1/execution-packages/{package_id}/run")
    assert repeated.status_code == 409


def test_rejected_execution_cancels_experiment() -> None:
    product_id, result = _build_growth_plays()
    play = result["plays"][0]
    _approve_play(product_id, play)
    prepared = client.post(
        f"/v1/products/{product_id}/growth-plays/{play['id']}/execution/prepare",
        json={"contact_email": "partner@example.com"},
    ).json()

    rejected = client.post(f"/v1/execution-packages/{prepared['id']}/reject")
    assert rejected.status_code == 200
    experiment = client.get(f"/v1/experiments/{prepared['experiment_id']}")
    assert experiment.status_code == 200
    assert experiment.json()["status"] == "CANCELLED"


def test_platform_contact_cannot_be_auto_sent() -> None:
    product_id, result = _build_growth_plays()
    play = result["plays"][0]
    _approve_play(product_id, play)
    prepared = client.post(
        f"/v1/products/{product_id}/growth-plays/{play['id']}/execution/prepare",
        json={},
    )
    assert prepared.status_code == 200
    package = prepared.json()
    assert package["contact"]["method"] == "platform"
    client.post(f"/v1/execution-packages/{package['id']}/approve")
    run = client.post(f"/v1/execution-packages/{package['id']}/run")
    assert run.status_code == 409


def test_contact_extractor_finds_public_email_in_evidence() -> None:
    channel = ChannelOpportunityView(
        id="00000000-0000-0000-0000-000000000001",
        icp_id="00000000-0000-0000-0000-000000000002",
        source_type="newsletter_site",
        platform="example.com",
        title="Example Publication",
        url="https://example.com/contact",
        relevance_score=80,
        rationale="Relevant audience",
        evidence=[
            ChannelEvidenceView(
                query="example publication contact",
                title="Contact",
                url="https://example.com/contact",
                snippet="Partnership inquiries: editor@example.com",
            )
        ],
    )
    target = ContactExtractor().extract(channel)
    assert target.method == "email"
    assert target.address == "editor@example.com"
    assert target.source == "public_evidence"
