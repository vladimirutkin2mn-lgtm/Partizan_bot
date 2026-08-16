from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.autonomous_controlled_growth import autonomous_controlled_growth_sweep_service
from app.autonomy_schemas import GrowthMandateUpsertRequest
from app.autonomy_service import growth_mandate_service
from app.config import get_settings
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.icp_service import icp_service
from app.main import app
from app.outreach_autosend import (
    outreach_autonomous_send_service,
    outreach_autosend_delegation_service,
)
from app.outreach_briefs import outreach_brief_service
from app.outreach_policy import outreach_policy_service
from app.outreach_sender import (
    OutreachSMTPAmbiguousError,
    OutreachSMTPRejectedError,
    outreach_sender_service,
)
from app.outreach_targets import outreach_target_service
from app.product_intake import product_intake_service

client = TestClient(app)


class RecordingProvider:
    def __init__(self, outcomes: list[str] | None = None) -> None:
        self.outcomes = list(outcomes or ["sent"])
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
        outcome = self.outcomes.pop(0) if self.outcomes else "sent"
        if outcome == "ambiguous":
            raise OutreachSMTPAmbiguousError("connection dropped after DATA")
        if outcome == "rejected":
            raise OutreachSMTPRejectedError("recipient rejected")
        return f"<accepted-{len(self.calls)}@oracle.com>"


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
    audience_intelligence_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    distribution_analytics_service.reset()
    growth_mandate_service.reset()
    outreach_target_service.reset()
    outreach_brief_service.reset()
    outreach_sender_service.reset()
    outreach_policy_service.reset()
    outreach_autosend_delegation_service.reset()
    outreach_autonomous_send_service.reset()
    outreach_sender_service._provider = None
    yield
    outreach_sender_service._provider = None
    get_settings.cache_clear()


def _product_and_opportunity() -> tuple[str, dict]:
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
    return product_id, opportunity


def _mandate(product_id: str, *, autonomous_approve: bool) -> None:
    growth_mandate_service.upsert(
        UUID(product_id),
        GrowthMandateUpsertRequest(
            total_budget_cap=1000,
            target_max_cac=12,
            max_autonomous_spend_per_experiment=0,
            max_autonomous_spend_per_day=0,
            max_concurrent_running_experiments=5,
            allowed_platforms=list(DistributionPlatform),
            allowed_actions=[DistributionActionType.OUTREACH_EMAIL],
            autonomous_prepare=True,
            autonomous_approve=autonomous_approve,
            autonomous_paid_activation=False,
        ),
    )


def _policy(product_id: str, *, daily_send_cap: int = 3) -> dict:
    response = client.put(
        f"/v1/products/{product_id}/outreach-policy",
        json={
            "minimum_target_confidence": 75,
            "allowed_target_types": ["CREATOR", "PARTNER"],
            "allowed_contact_provenance": [
                "OPERATOR_SUPPLIED",
                "PUBLIC_BUSINESS_SOURCE",
            ],
            "max_prepared_per_day": 5,
            "max_prepared_per_domain_per_day": 1,
            "max_initial_sends_per_day": daily_send_cap,
            "max_initial_sends_per_domain_per_day": 1,
            "target_cooldown_days": 30,
            "domain_cooldown_hours": 24,
            "require_sender_ready_before_prepare": True,
        },
    )
    assert response.status_code == 200
    return response.json()


def _delegate(product_id: str) -> dict:
    response = client.post(
        f"/v1/products/{product_id}/outreach-autosend/delegate",
        json={"confirm_autonomous_initial_send": True},
    )
    assert response.status_code == 200
    return response.json()


def _target(
    product_id: str,
    opportunity: dict,
    *,
    email: str,
    confidence: float,
    name: str,
) -> dict:
    response = client.post(
        f"/v1/products/{product_id}/outreach-targets",
        json={
            "opportunity_id": opportunity["id"],
            "target_type": "CREATOR",
            "canonical_name": name,
            "target_url": opportunity["url"],
            "business_email": email,
            "contact_evidence": {
                "provenance_type": "OPERATOR_SUPPLIED",
                "source_label": "Business address supplied by operator",
            },
            "relevance_rationale": "The creator publishes content relevant to the confirmed ICP.",
            "icp_overlap_rationale": "The opportunity was discovered for this product's ICP.",
            "confidence": confidence,
            "language": "English",
            "jurisdiction": "US",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_autosend_delegation_is_scoped_from_global_autonomous_approval() -> None:
    product_id, _ = _product_and_opportunity()
    _mandate(product_id, autonomous_approve=False)
    _policy(product_id)

    delegated = _delegate(product_id)

    mandate = growth_mandate_service.get(UUID(product_id))
    assert mandate.autonomous_approve is False
    assert delegated["status"] == "ACTIVE"
    assert delegated["max_followups"] == 0
    assert delegated["max_initial_sends_per_day"] == 3


@pytest.mark.asyncio
async def test_autonomous_sweep_sends_one_delegated_initial_message() -> None:
    product_id, opportunity = _product_and_opportunity()
    _mandate(product_id, autonomous_approve=False)
    target = _target(
        product_id,
        opportunity,
        email="collabs@creator-one.com",
        confidence=94,
        name="Creator One",
    )
    _policy(product_id)
    _delegate(product_id)
    provider = RecordingProvider()
    outreach_sender_service._provider = provider

    sweep = await autonomous_controlled_growth_sweep_service.run_once(UUID(product_id))

    decision = next(item for item in sweep.decisions if item.action_type == "OUTREACH_EMAIL")
    assert decision.outcome.value == "EXECUTED"
    assert decision.evaluation_decision.value == "ALLOW"
    assert len(provider.calls) == 1
    assert provider.calls[0]["to_email"] == target["business_email"]
    brief = outreach_brief_service.list_target(UUID(target["id"])).briefs[0]
    attempt = outreach_sender_service.get_attempt(brief.id)
    assert attempt is not None
    assert attempt.status.value == "SENT"
    action = distribution_execution_service.get_action(brief.action_id)
    experiment = distribution_execution_service.get_experiment(brief.experiment_id)
    assert action.status.value == "EXECUTED"
    assert experiment.status.value == "RUNNING"


@pytest.mark.asyncio
async def test_without_autosend_delegation_sweep_still_waits_for_approval() -> None:
    product_id, opportunity = _product_and_opportunity()
    _mandate(product_id, autonomous_approve=False)
    target = _target(
        product_id,
        opportunity,
        email="manual@creator-two.com",
        confidence=91,
        name="Creator Two",
    )
    _policy(product_id)
    provider = RecordingProvider()
    outreach_sender_service._provider = provider

    sweep = await autonomous_controlled_growth_sweep_service.run_once(UUID(product_id))

    decision = next(item for item in sweep.decisions if item.action_type == "OUTREACH_EMAIL")
    assert decision.outcome.value == "WAITING_APPROVAL"
    assert len(provider.calls) == 0
    brief = outreach_brief_service.list_target(UUID(target["id"])).briefs[0]
    assert outreach_sender_service.get_attempt(brief.id) is None


@pytest.mark.asyncio
async def test_policy_change_invalidates_autosend_without_blocking_manual_preparation() -> None:
    product_id, opportunity = _product_and_opportunity()
    _mandate(product_id, autonomous_approve=False)
    target = _target(
        product_id,
        opportunity,
        email="changed@creator-three.com",
        confidence=90,
        name="Creator Three",
    )
    _policy(product_id)
    _delegate(product_id)
    _policy(product_id, daily_send_cap=2)
    provider = RecordingProvider()
    outreach_sender_service._provider = provider

    sweep = await autonomous_controlled_growth_sweep_service.run_once(UUID(product_id))

    decision = next(item for item in sweep.decisions if item.action_type == "OUTREACH_EMAIL")
    assert decision.outcome.value == "WAITING_APPROVAL"
    assert len(provider.calls) == 0
    brief = outreach_brief_service.list_target(UUID(target["id"])).briefs[0]
    assert outreach_sender_service.get_attempt(brief.id) is None


@pytest.mark.asyncio
async def test_daily_send_cap_blocks_second_autonomous_message() -> None:
    product_id, opportunity = _product_and_opportunity()
    _mandate(product_id, autonomous_approve=False)
    first = _target(
        product_id,
        opportunity,
        email="first@creator-a.com",
        confidence=96,
        name="First Creator",
    )
    second = _target(
        product_id,
        opportunity,
        email="second@creator-b.com",
        confidence=92,
        name="Second Creator",
    )
    assert first["id"] != second["id"]
    _policy(product_id, daily_send_cap=1)
    _delegate(product_id)
    provider = RecordingProvider(["sent", "sent"])
    outreach_sender_service._provider = provider

    first_sweep = await autonomous_controlled_growth_sweep_service.run_once(UUID(product_id))
    second_sweep = await autonomous_controlled_growth_sweep_service.run_once(UUID(product_id))

    first_decision = next(
        item for item in first_sweep.decisions if item.action_type == "OUTREACH_EMAIL"
    )
    second_decision = next(
        item for item in second_sweep.decisions if item.action_type == "OUTREACH_EMAIL"
    )
    assert first_decision.outcome.value == "EXECUTED"
    assert second_decision.outcome.value == "BLOCKED"
    assert len(provider.calls) == 1
    assert "capacity" in " ".join(second_decision.reasons).lower()


@pytest.mark.asyncio
async def test_ambiguous_send_blocks_future_autonomous_outreach_without_retry() -> None:
    product_id, opportunity = _product_and_opportunity()
    _mandate(product_id, autonomous_approve=False)
    first = _target(
        product_id,
        opportunity,
        email="ambiguous@creator-c.com",
        confidence=97,
        name="Ambiguous Creator",
    )
    second = _target(
        product_id,
        opportunity,
        email="next@creator-d.com",
        confidence=89,
        name="Next Creator",
    )
    assert first["id"] != second["id"]
    _policy(product_id)
    _delegate(product_id)
    provider = RecordingProvider(["ambiguous", "sent"])
    outreach_sender_service._provider = provider

    first_sweep = await autonomous_controlled_growth_sweep_service.run_once(UUID(product_id))
    second_sweep = await autonomous_controlled_growth_sweep_service.run_once(UUID(product_id))

    first_decision = next(
        item for item in first_sweep.decisions if item.action_type == "OUTREACH_EMAIL"
    )
    second_decision = next(
        item for item in second_sweep.decisions if item.action_type == "OUTREACH_EMAIL"
    )
    assert first_decision.outcome.value == "BLOCKED"
    assert second_decision.outcome.value == "BLOCKED"
    assert len(provider.calls) == 1
    assert "reconciliation" in " ".join(second_decision.reasons).lower()


@pytest.mark.asyncio
async def test_definitive_reject_is_cancelled_and_next_target_can_run() -> None:
    product_id, opportunity = _product_and_opportunity()
    _mandate(product_id, autonomous_approve=False)
    rejected_target = _target(
        product_id,
        opportunity,
        email="reject@creator-e.com",
        confidence=98,
        name="Rejected Creator",
    )
    next_target = _target(
        product_id,
        opportunity,
        email="next@creator-f.com",
        confidence=90,
        name="Next Good Creator",
    )
    _policy(product_id)
    _delegate(product_id)
    provider = RecordingProvider(["rejected", "sent"])
    outreach_sender_service._provider = provider

    first_sweep = await autonomous_controlled_growth_sweep_service.run_once(UUID(product_id))
    first_decision = next(
        item for item in first_sweep.decisions if item.action_type == "OUTREACH_EMAIL"
    )
    assert first_decision.outcome.value == "FAILED"
    rejected_brief = outreach_brief_service.list_target(UUID(rejected_target["id"])).briefs[0]
    rejected_action = distribution_execution_service.get_action(rejected_brief.action_id)
    rejected_experiment = distribution_execution_service.get_experiment(
        rejected_brief.experiment_id
    )
    assert rejected_action.status.value == "SKIPPED"
    assert rejected_experiment.status.value == "CANCELLED"

    second_sweep = await autonomous_controlled_growth_sweep_service.run_once(UUID(product_id))
    second_decision = next(
        item for item in second_sweep.decisions if item.action_type == "OUTREACH_EMAIL"
    )
    assert second_decision.outcome.value == "EXECUTED"
    assert len(provider.calls) == 2
    assert provider.calls[1]["to_email"] == next_target["business_email"]
