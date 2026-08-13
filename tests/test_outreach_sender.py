from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.channel_service import channel_service
from app.config import get_settings
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.main import app
from app.outreach_briefs import outreach_brief_service
from app.outreach_sender import (
    OUTREACH_SEND_ATTEMPT_NAMESPACE,
    OutreachSendAttemptStatus,
    OutreachSendAttemptView,
    OutreachSMTPAmbiguousError,
    OutreachSMTPRejectedError,
    outreach_sender_service,
)
from app.outreach_targets import outreach_target_service
from app.product_intake import product_intake_service
from app.runtime_store import MemoryRuntimeStateStore

client = TestClient(app)


class RecordingProvider:
    def __init__(self, outcome: str = "sent") -> None:
        self.outcome = outcome
        self.calls: list[dict[str, str]] = []

    async def send(
        self,
        *,
        to_email: str,
        subject: str,
        body: str,
        fingerprint: str,
    ) -> str:
        self.calls.append(
            {
                "to_email": to_email,
                "subject": subject,
                "body": body,
                "fingerprint": fingerprint,
            }
        )
        if self.outcome == "ambiguous":
            raise OutreachSMTPAmbiguousError("connection dropped after DATA")
        if self.outcome == "rejected":
            raise OutreachSMTPRejectedError("recipient rejected")
        return "<provider-confirmed-message@oracle.com>"


@pytest.fixture(autouse=True)
def reset_state(monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_PROVIDER", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.oracle.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "oracle-growth")
    monkeypatch.setenv("SMTP_PASSWORD", "super-secret-password")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "growth@oracle.com")
    monkeypatch.setenv("SMTP_FROM_NAME", "Oracle Growth")
    monkeypatch.setenv("SMTP_REPLY_TO", "founder@oracle.com")
    monkeypatch.setenv("SMTP_STARTTLS", "true")
    get_settings.cache_clear()

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
    outreach_sender_service.reset()
    outreach_sender_service._provider = None
    yield
    outreach_sender_service._provider = None
    get_settings.cache_clear()


def _product_target_brief() -> tuple[str, dict, dict]:
    product_response = client.post(
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
    product_id = product_response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    discovery = client.post(f"/v1/products/{product_id}/distribution/discover")
    assert discovery.status_code == 200
    opportunity = next(item for item in discovery.json()["opportunities"] if item.get("url"))

    target_response = client.post(
        f"/v1/products/{product_id}/outreach-targets",
        json={
            "opportunity_id": opportunity["id"],
            "target_type": "CREATOR",
            "canonical_name": opportunity["title"],
            "target_url": opportunity["url"],
            "business_email": "collabs@creator.com",
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
    assert target_response.status_code == 201
    target = target_response.json()

    brief_response = client.post(
        f"/v1/outreach-targets/{target['id']}/briefs",
        json={"preferred_offer_type": "CREATOR_SEEDING"},
    )
    assert brief_response.status_code == 201
    return product_id, target, brief_response.json()


def _authorize(brief: dict, target: dict) -> dict:
    response = client.post(
        f"/v1/outreach-briefs/{brief['id']}/send-authorizations",
        json={
            "recipient_email": target["business_email"],
            "confirm_one_initial_message": True,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_sender_readiness_is_safe_and_does_not_expose_credentials() -> None:
    response = client.get("/v1/outreach/sender-readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert payload["from_email"] == "growth@oracle.com"
    assert payload["from_name"] == "Oracle Growth"
    assert payload["reply_to"] == "founder@oracle.com"
    serialized = response.text.lower()
    assert "super-secret-password" not in serialized
    assert "smtp.oracle.com" not in serialized
    assert "oracle-growth" not in serialized


def test_authorization_requires_exact_recipient_and_explicit_confirmation() -> None:
    _, target, brief = _product_target_brief()

    no_confirmation = client.post(
        f"/v1/outreach-briefs/{brief['id']}/send-authorizations",
        json={"recipient_email": target["business_email"]},
    )
    wrong_recipient = client.post(
        f"/v1/outreach-briefs/{brief['id']}/send-authorizations",
        json={
            "recipient_email": "other@creator.com",
            "confirm_one_initial_message": True,
        },
    )

    assert no_confirmation.status_code == 409
    assert "confirmation" in no_confirmation.json()["detail"].lower()
    assert wrong_recipient.status_code == 409
    assert "evidence-backed" in wrong_recipient.json()["detail"]

    authorization = _authorize(brief, target)
    assert authorization["status"] == "AUTHORIZED"
    serialized = str(authorization).lower()
    assert "super-secret-password" not in serialized
    assert authorization["message_fingerprint"]


def test_confirmed_send_runs_experiment_and_never_submits_twice() -> None:
    product_id, target, brief = _product_target_brief()
    authorization = _authorize(brief, target)
    provider = RecordingProvider()
    outreach_sender_service._provider = provider

    first = client.post(
        f"/v1/outreach-send-authorizations/{authorization['id']}/send"
    )
    repeated = client.post(
        f"/v1/outreach-send-authorizations/{authorization['id']}/send"
    )

    assert first.status_code == 200
    assert repeated.status_code == 200
    assert first.json()["id"] == repeated.json()["id"]
    assert first.json()["status"] == "SENT"
    assert len(provider.calls) == 1
    assert provider.calls[0]["to_email"] == target["business_email"]
    assert provider.calls[0]["body"].count(brief["tracking_url"]) == 1

    action = distribution_execution_service.get_action(UUID(brief["action_id"]))
    experiment = distribution_execution_service.get_experiment(UUID(brief["experiment_id"]))
    assert action.status.value == "EXECUTED"
    assert experiment.status.value == "RUNNING"
    assert action.operational_metadata["external_reference"] == (
        "<provider-confirmed-message@oracle.com>"
    )
    assert len(distribution_execution_service.list_experiments(UUID(product_id))) == 1

    stored_authorization = client.get(
        f"/v1/outreach-send-authorizations/{authorization['id']}"
    ).json()
    assert stored_authorization["status"] == "CONSUMED"


def test_ambiguous_submission_never_blind_retries() -> None:
    _, target, brief = _product_target_brief()
    authorization = _authorize(brief, target)
    provider = RecordingProvider("ambiguous")
    outreach_sender_service._provider = provider

    first = client.post(
        f"/v1/outreach-send-authorizations/{authorization['id']}/send"
    )
    repeated = client.post(
        f"/v1/outreach-send-authorizations/{authorization['id']}/send"
    )

    assert first.status_code == 200
    assert first.json()["status"] == "RECONCILIATION_REQUIRED"
    assert repeated.json()["id"] == first.json()["id"]
    assert len(provider.calls) == 1
    assert "connection dropped" not in first.text
    assert first.json()["error_code"] == "SMTP_OUTCOME_UNKNOWN"

    action = distribution_execution_service.get_action(UUID(brief["action_id"]))
    experiment = distribution_execution_service.get_experiment(UUID(brief["experiment_id"]))
    assert action.status.value == "APPROVED"
    assert experiment.status.value == "APPROVED"


def test_definitive_rejection_is_terminal_for_the_brief() -> None:
    _, target, brief = _product_target_brief()
    authorization = _authorize(brief, target)
    provider = RecordingProvider("rejected")
    outreach_sender_service._provider = provider

    first = client.post(
        f"/v1/outreach-send-authorizations/{authorization['id']}/send"
    )
    repeated = client.post(
        f"/v1/outreach-send-authorizations/{authorization['id']}/send"
    )

    assert first.status_code == 200
    assert first.json()["status"] == "REJECTED"
    assert repeated.json()["id"] == first.json()["id"]
    assert len(provider.calls) == 1
    assert first.json()["error_code"] == "SMTP_REJECTED"


def test_suppression_after_authorization_blocks_send_before_reservation() -> None:
    _, target, brief = _product_target_brief()
    authorization = _authorize(brief, target)
    provider = RecordingProvider()
    outreach_sender_service._provider = provider

    suppressed = client.post(
        f"/v1/outreach-targets/{target['id']}/suppress",
        json={"reason": "OPT_OUT", "note": "Recipient opted out before send."},
    )
    assert suppressed.status_code == 200

    response = client.post(
        f"/v1/outreach-send-authorizations/{authorization['id']}/send"
    )

    assert response.status_code == 409
    assert "OPT_OUT" in response.json()["detail"]
    assert provider.calls == []
    assert outreach_sender_service.get_attempt(UUID(brief["id"])) is None
    action = distribution_execution_service.get_action(UUID(brief["action_id"]))
    assert action.status.value == "PREPARED"


def test_message_change_after_authorization_requires_new_authorization() -> None:
    _, target, brief = _product_target_brief()
    authorization = _authorize(brief, target)
    provider = RecordingProvider()
    outreach_sender_service._provider = provider

    edited = client.patch(
        f"/v1/distribution-actions/{brief['action_id']}",
        json={
            "content_text": (
                f"Subject: changed\n\nChanged body with {brief['tracking_url']}"
            )
        },
    )
    assert edited.status_code == 200

    response = client.post(
        f"/v1/outreach-send-authorizations/{authorization['id']}/send"
    )

    assert response.status_code == 409
    assert "changed after authorization" in response.json()["detail"]
    assert provider.calls == []
    assert outreach_sender_service.get_attempt(UUID(brief["id"])) is None


def test_stale_started_attempt_becomes_reconciliation_required_without_send() -> None:
    _, target, brief = _product_target_brief()
    authorization = _authorize(brief, target)
    provider = RecordingProvider()
    outreach_sender_service._provider = provider
    old = datetime.now(UTC) - timedelta(minutes=10)
    attempt = OutreachSendAttemptView(
        id=UUID("00000000-0000-0000-0000-000000000123"),
        authorization_id=UUID(authorization["id"]),
        brief_id=UUID(brief["id"]),
        action_id=UUID(brief["action_id"]),
        experiment_id=UUID(brief["experiment_id"]),
        outreach_target_id=UUID(target["id"]),
        recipient_email=target["business_email"],
        sender_email="growth@oracle.com",
        message_fingerprint=authorization["message_fingerprint"],
        provider="SMTP",
        status=OutreachSendAttemptStatus.STARTED,
        started_at=old,
    )
    outreach_sender_service._store.put(
        OUTREACH_SEND_ATTEMPT_NAMESPACE,
        brief["id"],
        attempt.model_dump(mode="json"),
    )

    response = client.post(
        f"/v1/outreach-send-authorizations/{authorization['id']}/send"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "RECONCILIATION_REQUIRED"
    assert response.json()["error_code"] == "STALE_STARTED_ATTEMPT"
    assert provider.calls == []


def test_memory_runtime_reservation_is_atomic_for_one_key() -> None:
    store = MemoryRuntimeStateStore()
    assert store.put_if_absent("outreach", "brief-1", {"status": "STARTED"}) is True
    assert store.put_if_absent("outreach", "brief-1", {"status": "SECOND"}) is False
    assert store.get("outreach", "brief-1") == {"status": "STARTED"}
