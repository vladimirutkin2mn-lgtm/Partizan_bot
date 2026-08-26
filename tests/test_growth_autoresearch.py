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


def _service() -> tuple[GrowthAutoResearchService, MemoryRuntimeStateStore]:
    store = MemoryRuntimeStateStore()
    return GrowthAutoResearchService(store=store), store


def _policy(**updates) -> GrowthResearchPolicyRequest:
    values = {
        "allowed_platforms": ["META"],
        "max_changed_dimensions": 2,
        "max_shadow_trial_budget": 500,
        "min_paid_users_for_decision": 3,
        "min_relative_cac_improvement": 0.1,
        "confidence_level": 0.9,
    }
    values.update(updates)
    return GrowthResearchPolicyRequest(**values)


def _variant(**updates) -> GrowthVariantSpec:
    values = {
        "platform": "META",
        "tactic_id": "paid-social-founders",
        "audience": "solo founders",
        "message_angle": "save 10 hours per week",
        "offer": "free assessment",
        "creative_ref": "video-a",
        "cta": "Get started",
        "destination_url": "https://example.com/start",
        "targeting": "US founders",
        "timing": "always-on",
        "test_budget": 300,
    }
    values.update(updates)
    return GrowthVariantSpec(**values)


def _evidence(
    *,
    spend: float,
    paid: int,
    revenue: float = 0,
    visits: int = 500,
    signups: int = 80,
    activated: int = 50,
    duration_hours: float = 24,
) -> GrowthResearchEvidence:
    return GrowthResearchEvidence(
        spend=spend,
        impressions=5000,
        clicks=500,
        visits=visits,
        signups=signups,
        activated_users=activated,
        paid_users=paid,
        revenue=revenue,
        duration_hours=duration_hours,
    )


def _baseline(service: GrowthAutoResearchService, product_id):
    service.configure_policy(product_id, _policy())
    return service.establish_baseline(
        product_id,
        GrowthResearchBaselineRequest(
            variant=_variant(),
            evidence=_evidence(spend=400, paid=20, revenue=800),
        ),
    )


def test_keep_promotes_challenger_and_history_survives_service_restart() -> None:
    service, store = _service()
    product_id = uuid4()
    baseline = _baseline(service, product_id)

    trial = service.create_challenger(
        product_id,
        GrowthResearchChallengerRequest(
            variant=_variant(message_angle="replace manual reporting"),
        ),
    )
    evaluation = service.evaluate_trial(
        trial.id,
        GrowthResearchEvaluationRequest(
            evidence=_evidence(spend=300, paid=30, revenue=900),
        ),
    )

    assert evaluation.outcome == GrowthResearchOutcome.KEEP
    assert evaluation.objective == GrowthResearchObjective.PAID_CAC
    assert evaluation.confidence is not None
    assert evaluation.confidence >= 0.9
    assert evaluation.champion_cac == 20
    assert evaluation.challenger_cac == 10
    champion = service.current_champion(product_id)
    assert champion is not None
    assert champion.id != baseline.id
    assert champion.variant.message_angle == "replace manual reporting"
    assert champion.source_trial_id == trial.id

    restarted = GrowthAutoResearchService(store=store)
    history = restarted.history(product_id)
    assert history.champion is not None
    assert history.champion.id == champion.id
    assert [item.id for item in history.trials] == [trial.id]
    assert [item.outcome for item in history.evaluations] == [GrowthResearchOutcome.KEEP]
    assert history.evaluations[0].objective == GrowthResearchObjective.PAID_CAC


def test_discard_and_inconclusive_never_replace_champion() -> None:
    service, _ = _service()
    product_id = uuid4()
    baseline = _baseline(service, product_id)

    losing_trial = service.create_challenger(
        product_id,
        GrowthResearchChallengerRequest(variant=_variant(message_angle="generic productivity")),
    )
    losing_evaluation = service.evaluate_trial(
        losing_trial.id,
        GrowthResearchEvaluationRequest(
            evidence=_evidence(spend=300, paid=6, revenue=60),
        ),
    )
    assert losing_evaluation.outcome == GrowthResearchOutcome.DISCARD
    assert service.current_champion(product_id).id == baseline.id

    sparse_trial = service.create_challenger(
        product_id,
        GrowthResearchChallengerRequest(variant=_variant(audience="bootstrapped SaaS founders")),
    )
    sparse_evaluation = service.evaluate_trial(
        sparse_trial.id,
        GrowthResearchEvaluationRequest(
            evidence=_evidence(
                spend=20,
                paid=2,
                visits=20,
                signups=1,
                activated=1,
            )
        ),
    )
    assert sparse_evaluation.outcome == GrowthResearchOutcome.INCONCLUSIVE
    assert service.current_champion(product_id).id == baseline.id


def test_policy_rejects_unapproved_or_overbroad_challengers() -> None:
    service, _ = _service()
    product_id = uuid4()
    _baseline(service, product_id)

    with pytest.raises(ValueError, match="at least one"):
        service.create_challenger(
            product_id,
            GrowthResearchChallengerRequest(variant=_variant()),
        )

    with pytest.raises(ValueError, match="outside the Growth AutoResearch policy"):
        service.create_challenger(
            product_id,
            GrowthResearchChallengerRequest(variant=_variant(platform="TIKTOK")),
        )

    with pytest.raises(ValueError, match="exceeds the shadow research policy"):
        service.create_challenger(
            product_id,
            GrowthResearchChallengerRequest(variant=_variant(test_budget=501)),
        )

    with pytest.raises(ValueError, match="too many growth dimensions"):
        service.create_challenger(
            product_id,
            GrowthResearchChallengerRequest(
                variant=_variant(
                    audience="SMB founders",
                    message_angle="replace reporting",
                    creative_ref="video-b",
                )
            ),
        )

    service.configure_policy(product_id, _policy(paused=True))
    with pytest.raises(ValueError, match="paused"):
        service.create_challenger(
            product_id,
            GrowthResearchChallengerRequest(variant=_variant(message_angle="another angle")),
        )


def test_stale_trial_is_blocked_after_another_trial_advances_champion() -> None:
    service, _ = _service()
    product_id = uuid4()
    _baseline(service, product_id)

    winner = service.create_challenger(
        product_id,
        GrowthResearchChallengerRequest(variant=_variant(message_angle="winner angle")),
    )
    stale = service.create_challenger(
        product_id,
        GrowthResearchChallengerRequest(variant=_variant(audience="another founder segment")),
    )

    winner_result = service.evaluate_trial(
        winner.id,
        GrowthResearchEvaluationRequest(
            evidence=_evidence(spend=300, paid=30, revenue=900),
        ),
    )
    assert winner_result.outcome == GrowthResearchOutcome.KEEP
    winning_champion = service.current_champion(product_id)
    assert winning_champion is not None

    stale_result = service.evaluate_trial(
        stale.id,
        GrowthResearchEvaluationRequest(
            evidence=_evidence(spend=250, paid=30, revenue=900),
        ),
    )
    assert stale_result.outcome == GrowthResearchOutcome.BLOCKED
    assert "champion changed" in stale_result.rationale[0].lower()
    assert service.current_champion(product_id).id == winning_champion.id


def test_explicit_blocked_and_failed_results_are_persisted() -> None:
    service, _ = _service()
    product_id = uuid4()
    _baseline(service, product_id)

    blocked_trial = service.create_challenger(
        product_id,
        GrowthResearchChallengerRequest(variant=_variant(message_angle="blocked angle")),
    )
    blocked = service.evaluate_trial(
        blocked_trial.id,
        GrowthResearchEvaluationRequest(
            evidence=_evidence(
                spend=0,
                paid=0,
                visits=0,
                signups=0,
                activated=0,
            ),
            blocked_reason="Integration is unavailable in shadow replay.",
        ),
    )
    assert blocked.outcome == GrowthResearchOutcome.BLOCKED

    failed_trial = service.create_challenger(
        product_id,
        GrowthResearchChallengerRequest(variant=_variant(audience="failed segment")),
    )
    failed = service.evaluate_trial(
        failed_trial.id,
        GrowthResearchEvaluationRequest(
            evidence=_evidence(
                spend=0,
                paid=0,
                visits=0,
                signups=0,
                activated=0,
            ),
            failed_reason="Shadow fixture crashed.",
        ),
    )
    assert failed.outcome == GrowthResearchOutcome.FAILED

    assert [item.outcome for item in service.history(product_id).evaluations] == [
        GrowthResearchOutcome.BLOCKED,
        GrowthResearchOutcome.FAILED,
    ]
