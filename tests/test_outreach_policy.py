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
from app.outreach_briefs import outreach_brief_service
from app.outreach_policy import outreach_policy_service
from app.outreach_sender import outreach_sender_service
from app.outreach_targets import outreach_target_service
from app.product_intake import product_intake_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state(monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_PROVIDER", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.oracle.com")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "growth@oracle.com")
    monkeypatch.setenv("SMTP_FROM_NAME", "Oracle Growth")
    monkeypatch.setenv("SMTP_REPLY_TO", "founder@oracle.com")
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
    yield
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


def _mandate(product_id: str, *, autonomous_prepare: bool = True) -> None:
    growth_mandate_service.upsert(
        UUID(product_id),
        GrowthMandateUpsertRequest(
            total_budget_cap=1000,
            target_max_cac=12,
            max_autonomous_spend_per_experiment=0,
            max_autonomous_spend_per_day=0,
            max_concurrent_running_experiments=3,
            allowed_platforms=list(DistributionPlatform),
            allowed_actions=[DistributionActionType.OUTREACH_EMAIL],
            autonomous_prepare=autonomous_prepare,
            autonomous_approve=False,
            autonomous_paid_activation=False,
        ),
    )


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


def _policy_payload() -> dict:
    return {
        "minimum_target_confidence": 75,
        "allowed_target_types": ["CREATOR", "PARTNER"],
        "allowed_contact_provenance": ["OPERATOR_SUPPLIED", "PUBLIC_BUSINESS_SOURCE"],
        "max_prepared_per_day": 3,
        "max_prepared_per_domain_per_day": 1,
        "max_initial_sends_per_day": 3,
        "max_initial_sends_per_domain_per_day": 1,
        "target_cooldown_days": 30,
        "domain_cooldown_hours": 24,
        "require_sender_ready_before_prepare": True,
    }


def test_policy_requires_explicit_growth_mandate_permission() -> None:
    product_id, _ = _product_and_opportunity()

    without_mandate = client.put(
        f"/v1/products/{product_id}/outreach-policy",
        json=_policy_payload(),
    )
    assert without_mandate.status_code == 409

    growth_mandate_service.upsert(
        UUID(product_id),
        GrowthMandateUpsertRequest(
            total_budget_cap=1000,
            max_autonomous_spend_per_experiment=0,
            max_autonomous_spend_per_day=0,
            allowed_platforms=list(DistributionPlatform),
            allowed_actions=[DistributionActionType.COMMENT],
            autonomous_prepare=True,
            autonomous_approve=False,
            autonomous_paid_activation=False,
        ),
    )
    not_allowed = client.put(
        f"/v1/products/{product_id}/outreach-policy",
        json=_policy_payload(),
    )
    assert not_allowed.status_code == 409
    assert "OUTREACH_EMAIL" in not_allowed.json()["detail"]


def test_policy_hard_caps_cannot_be_expanded_by_operator() -> None:
    product_id, _ = _product_and_opportunity()
    _mandate(product_id)
    payload = _policy_payload()
    payload["max_initial_sends_per_day"] = 6

    response = client.put(
        f"/v1/products/{product_id}/outreach-policy",
        json=payload,
    )

    assert response.status_code == 422


def test_autonomous_preparation_selects_highest_confidence_and_does_not_send() -> None:
    product_id, opportunity = _product_and_opportunity()
    _mandate(product_id)
    low = _target(
        product_id,
        opportunity,
        email="low@creators-one.com",
        confidence=78,
        name="Lower confidence creator",
    )
    high = _target(
        product_id,
        opportunity,
        email="high@creators-two.com",
        confidence=94,
        name="Higher confidence creator",
    )
    policy = client.put(
        f"/v1/products/{product_id}/outreach-policy",
        json=_policy_payload(),
    )
    assert policy.status_code == 200
    assert policy.json()["automatic_send_enabled"] is False
    assert policy.json()["max_followups"] == 0

    result = client.post(f"/v1/products/{product_id}/outreach-autonomy/prepare-next")

    assert result.status_code == 200
    prepared = result.json()
    assert prepared["prepared"] is True
    assert prepared["target_id"] == high["id"]
    assert prepared["target_id"] != low["id"]
    action = distribution_execution_service.get_action(UUID(prepared["action_id"]))
    experiment = distribution_execution_service.get_experiment(UUID(prepared["experiment_id"]))
    assert action.status.value == "PREPARED"
    assert experiment.status.value == "DRAFT"
    assert outreach_sender_service.get_attempt(UUID(prepared["brief_id"])) is None


def test_growth_mandate_change_invalidates_existing_outreach_policy() -> None:
    product_id, opportunity = _product_and_opportunity()
    _mandate(product_id)
    _target(
        product_id,
        opportunity,
        email="creator@mandate-change.com",
        confidence=90,
        name="Creator",
    )
    policy = client.put(
        f"/v1/products/{product_id}/outreach-policy",
        json=_policy_payload(),
    )
    assert policy.status_code == 200

    _mandate(product_id)
    result = client.post(f"/v1/products/{product_id}/outreach-autonomy/prepare-next")

    assert result.status_code == 200
    assert result.json()["prepared"] is False
    assert "changed" in " ".join(result.json()["reasons"]).lower()
    assert distribution_execution_service.list_experiments(UUID(product_id)) == []


def test_domain_cooldown_skips_second_contact_on_same_domain() -> None:
    product_id, opportunity = _product_and_opportunity()
    _mandate(product_id)
    first = _target(
        product_id,
        opportunity,
        email="first@same-domain.com",
        confidence=96,
        name="First creator",
    )
    second = _target(
        product_id,
        opportunity,
        email="second@same-domain.com",
        confidence=92,
        name="Second creator",
    )
    third = _target(
        product_id,
        opportunity,
        email="third@other-domain.com",
        confidence=88,
        name="Third creator",
    )
    assert first["id"] != second["id"] != third["id"]
    assert client.put(
        f"/v1/products/{product_id}/outreach-policy",
        json=_policy_payload(),
    ).status_code == 200

    first_run = client.post(f"/v1/products/{product_id}/outreach-autonomy/prepare-next").json()
    assert first_run["target_id"] == first["id"]
    distribution_execution_service.skip(UUID(first_run["action_id"]))

    second_run = client.post(f"/v1/products/{product_id}/outreach-autonomy/prepare-next").json()
    assert second_run["prepared"] is True
    assert second_run["target_id"] == third["id"]
    assert second_run["target_id"] != second["id"]


@pytest.mark.asyncio
async def test_existing_autonomous_sweep_surfaces_outreach_as_waiting_approval() -> None:
    product_id, opportunity = _product_and_opportunity()
    _mandate(product_id)
    target = _target(
        product_id,
        opportunity,
        email="sweep@creator-sweep.com",
        confidence=91,
        name="Sweep creator",
    )
    assert client.put(
        f"/v1/products/{product_id}/outreach-policy",
        json=_policy_payload(),
    ).status_code == 200

    sweep = await autonomous_controlled_growth_sweep_service.run_once(UUID(product_id))

    decision = next(item for item in sweep.decisions if item.action_type == "OUTREACH_EMAIL")
    assert decision.outcome.value == "WAITING_APPROVAL"
    assert decision.action_id is not None
    assert decision.experiment_id is not None
    brief = outreach_brief_service.list_target(UUID(target["id"])).briefs[0]
    assert brief.action_id == decision.action_id
    assert outreach_sender_service.get_attempt(brief.id) is None
