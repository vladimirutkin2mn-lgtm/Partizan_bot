from uuid import uuid4

import pytest

from app.growth_autoresearch import GrowthAutoResearchService
from app.growth_autoresearch_schemas import (
    GrowthResearchBaselineRequest,
    GrowthResearchChallengerRequest,
    GrowthResearchEvaluationRequest,
    GrowthResearchEvidence,
    GrowthResearchObjective,
    GrowthResearchOutcome,
    GrowthResearchPolicyRequest,
    GrowthVariantSpec,
)
from app.runtime_store import MemoryRuntimeStateStore


def _service() -> GrowthAutoResearchService:
    return GrowthAutoResearchService(store=MemoryRuntimeStateStore())


def _policy(**updates) -> GrowthResearchPolicyRequest:
    values = {
        "allowed_platforms": ["META"],
        "max_shadow_trial_budget": 500,
        "min_paid_users_for_decision": 3,
        "min_activated_users_for_decision": 5,
        "min_signups_for_decision": 10,
        "min_visits_for_proxy_decision": 100,
        "min_relative_cac_improvement": 0.1,
        "min_relative_proxy_improvement": 0.1,
        "confidence_level": 0.9,
    }
    values.update(updates)
    return GrowthResearchPolicyRequest(**values)


def _variant(**updates) -> GrowthVariantSpec:
    values = {
        "platform": "META",
        "tactic_id": "paid-social",
        "audience": "founders",
        "message_angle": "baseline angle",
        "test_budget": 300,
    }
    values.update(updates)
    return GrowthVariantSpec(**values)


def _evidence(**updates) -> GrowthResearchEvidence:
    values = {
        "spend": 0,
        "impressions": 0,
        "clicks": 0,
        "visits": 0,
        "signups": 0,
        "activated_users": 0,
        "paid_users": 0,
        "revenue": 0,
        "duration_hours": 24,
    }
    values.update(updates)
    return GrowthResearchEvidence(**values)


def _setup(
    service: GrowthAutoResearchService,
    *,
    policy: GrowthResearchPolicyRequest | None = None,
    baseline_evidence: GrowthResearchEvidence,
    baseline_variant: GrowthVariantSpec | None = None,
):
    product_id = uuid4()
    service.configure_policy(product_id, policy or _policy())
    champion = service.establish_baseline(
        product_id,
        GrowthResearchBaselineRequest(
            variant=baseline_variant or _variant(),
            evidence=baseline_evidence,
        ),
    )
    return product_id, champion


def _trial(
    service: GrowthAutoResearchService,
    product_id,
    *,
    message_angle: str = "challenger angle",
    **variant_updates,
):
    return service.create_challenger(
        product_id,
        GrowthResearchChallengerRequest(
            variant=_variant(message_angle=message_angle, **variant_updates),
        ),
    )


def test_one_paid_conversion_and_high_ctr_cannot_promote() -> None:
    service = _service()
    product_id, baseline = _setup(
        service,
        baseline_evidence=_evidence(
            spend=100,
            impressions=10000,
            clicks=100,
            visits=1000,
            paid_users=1,
            revenue=100,
        ),
    )
    trial = _trial(service, product_id)

    evaluation = service.evaluate_trial(
        trial.id,
        GrowthResearchEvaluationRequest(
            evidence=_evidence(
                spend=50,
                impressions=10000,
                clicks=1000,
                visits=1000,
                paid_users=1,
                revenue=100,
            )
        ),
    )

    assert evaluation.outcome == GrowthResearchOutcome.INCONCLUSIVE
    assert evaluation.objective == GrowthResearchObjective.NONE
    assert any("CTR/click" in item for item in evaluation.rationale)
    assert service.current_champion(product_id).id == baseline.id


def test_material_cac_point_estimate_stays_inconclusive_without_confidence() -> None:
    service = _service()
    product_id, baseline = _setup(
        service,
        baseline_evidence=_evidence(spend=60, paid_users=3, revenue=90),
    )
    trial = _trial(service, product_id)

    evaluation = service.evaluate_trial(
        trial.id,
        GrowthResearchEvaluationRequest(
            evidence=_evidence(spend=45, paid_users=3, revenue=90),
        ),
    )

    assert evaluation.objective == GrowthResearchObjective.PAID_CAC
    assert evaluation.relative_improvement == pytest.approx(0.25)
    assert evaluation.confidence is not None
    assert evaluation.confidence < 0.9
    assert evaluation.outcome == GrowthResearchOutcome.INCONCLUSIVE
    assert service.current_champion(product_id).id == baseline.id


def test_activation_is_used_only_when_purchase_economics_are_not_ready() -> None:
    service = _service()
    product_id, _ = _setup(
        service,
        baseline_evidence=_evidence(
            visits=200,
            signups=50,
            activated_users=20,
        ),
    )
    trial = _trial(service, product_id)

    evaluation = service.evaluate_trial(
        trial.id,
        GrowthResearchEvaluationRequest(
            evidence=_evidence(
                visits=200,
                signups=70,
                activated_users=40,
            )
        ),
    )

    assert evaluation.objective == GrowthResearchObjective.ACTIVATION_CONVERSION
    assert evaluation.outcome == GrowthResearchOutcome.KEEP
    assert evaluation.confidence is not None
    assert evaluation.confidence >= 0.9


def test_signup_is_used_when_activation_evidence_is_not_ready() -> None:
    service = _service()
    product_id, _ = _setup(
        service,
        baseline_evidence=_evidence(
            visits=200,
            signups=20,
            activated_users=1,
        ),
    )
    trial = _trial(service, product_id)

    evaluation = service.evaluate_trial(
        trial.id,
        GrowthResearchEvaluationRequest(
            evidence=_evidence(
                visits=200,
                signups=40,
                activated_users=1,
            )
        ),
    )

    assert evaluation.objective == GrowthResearchObjective.SIGNUP_CONVERSION
    assert evaluation.outcome == GrowthResearchOutcome.KEEP


def test_roas_guardrail_prevents_cac_only_promotion() -> None:
    service = _service()
    product_id, baseline = _setup(
        service,
        baseline_evidence=_evidence(
            spend=400,
            visits=500,
            signups=80,
            activated_users=50,
            paid_users=20,
            revenue=800,
        ),
    )
    trial = _trial(service, product_id)

    evaluation = service.evaluate_trial(
        trial.id,
        GrowthResearchEvaluationRequest(
            evidence=_evidence(
                spend=300,
                visits=500,
                signups=80,
                activated_users=50,
                paid_users=30,
                revenue=100,
            )
        ),
    )

    assert evaluation.objective == GrowthResearchObjective.PAID_CAC
    assert evaluation.confidence is not None
    assert evaluation.confidence >= 0.9
    assert evaluation.outcome == GrowthResearchOutcome.INCONCLUSIVE
    assert any("ROAS regressed" in item for item in evaluation.rationale)
    assert service.current_champion(product_id).id == baseline.id


def test_research_budget_share_caps_planned_challenger_budget() -> None:
    service = _service()
    policy = _policy(
        shadow_research_budget=100,
        max_trial_budget_share=0.2,
    )
    product_id, _ = _setup(
        service,
        policy=policy,
        baseline_variant=_variant(test_budget=10),
        baseline_evidence=_evidence(visits=200, signups=20),
    )

    with pytest.raises(ValueError, match="research-budget share"):
        _trial(service, product_id, test_budget=21)


def test_duration_and_planned_spend_protocol_violations_fail_closed() -> None:
    service = _service()
    policy = _policy(max_trial_duration_hours=48)
    product_id, baseline = _setup(
        service,
        policy=policy,
        baseline_variant=_variant(test_budget=100),
        baseline_evidence=_evidence(
            spend=100,
            visits=500,
            signups=80,
            activated_users=50,
            paid_users=10,
            revenue=200,
        ),
    )

    duration_trial = _trial(service, product_id, test_budget=100)
    duration_result = service.evaluate_trial(
        duration_trial.id,
        GrowthResearchEvaluationRequest(
            evidence=_evidence(
                spend=50,
                visits=500,
                signups=80,
                activated_users=50,
                paid_users=10,
                revenue=200,
                duration_hours=72,
            )
        ),
    )
    assert duration_result.outcome == GrowthResearchOutcome.FAILED
    assert any("duration" in item.lower() for item in duration_result.rationale)

    spend_trial = _trial(service, product_id, message_angle="second challenger", test_budget=100)
    spend_result = service.evaluate_trial(
        spend_trial.id,
        GrowthResearchEvaluationRequest(
            evidence=_evidence(
                spend=101,
                visits=500,
                signups=80,
                activated_users=50,
                paid_users=10,
                revenue=200,
            )
        ),
    )
    assert spend_result.outcome == GrowthResearchOutcome.FAILED
    assert any("planned test budget" in item.lower() for item in spend_result.rationale)
    assert service.current_champion(product_id).id == baseline.id
