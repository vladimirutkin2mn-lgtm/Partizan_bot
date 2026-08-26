from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from app.audience_intelligence_service import AUDIENCE_OPPORTUNITY_NAMESPACE
from app.distribution_analytics_service import (
    DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE,
    DISTRIBUTION_SPEND_NAMESPACE,
)
from app.distribution_execution_schemas import (
    DistributionExperimentStatus,
    DistributionExperimentView,
)
from app.distribution_execution_service import DISTRIBUTION_EXPERIMENT_NAMESPACE
from app.distribution_play_schemas import (
    DistributionPlayGenerationResponse,
    DistributionPlayStatus,
    DistributionPlayView,
    DistributionTacticClass,
)
from app.distribution_play_service import DISTRIBUTION_PLAY_NAMESPACE
from app.distribution_schemas import DistributionOpportunityView
from app.distribution_types import (
    AttributionLevel,
    AutomationLevel,
    DistributionActionType,
    DistributionPlatform,
    OpportunityKind,
)
from app.models import ProductProfileStatus
from app.product_intake import PRODUCT_INTAKE_NAMESPACE
from app.runtime_store import MemoryRuntimeStateStore
from app.schemas import ProductProfileView
from app.self_research_benchmark import SelfResearchBenchmarkBuilder
from app.self_research_benchmark_schemas import (
    SelfResearchComparisonOutcome,
    SelfResearchEvaluationInput,
    SelfResearchEvaluationMetrics,
    SelfResearchEvaluationView,
    SelfResearchCasePrediction,
)
from app.self_research_evaluator import SelfResearchEvaluator
from app.self_research_policy import (
    is_self_research_path_editable,
    is_self_research_path_protected,
)

PRODUCT_ID = UUID("11111111-1111-4111-8111-111111111111")
ICP_ID = UUID("22222222-2222-4222-8222-222222222222")
OPPORTUNITY_A = UUID("33333333-3333-4333-8333-333333333333")
OPPORTUNITY_B = UUID("44444444-4444-4444-8444-444444444444")
PLAY_A = UUID("55555555-5555-4555-8555-555555555555")
PLAY_B = UUID("66666666-6666-4666-8666-666666666666")
ACTION_A = UUID("77777777-7777-4777-8777-777777777777")
ACTION_B = UUID("88888888-8888-4888-8888-888888888888")
EXPERIMENT_A = UUID("99999999-9999-4999-8999-999999999999")
EXPERIMENT_B = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _seed_store() -> MemoryRuntimeStateStore:
    store = MemoryRuntimeStateStore()
    product = ProductProfileView(
        id=PRODUCT_ID,
        input_brief="SECRET RAW BRIEF must never enter the benchmark",
        name="Sensitive internal customer product name",
        description="Sensitive long description",
        problem_or_desire="Sensitive problem",
        value_proposition="Sensitive value proposition",
        usp="Sensitive USP",
        use_cases=["Sensitive use case"],
        market="US",
        language="English",
        price=49,
        pricing_model="one-off",
        goal="Acquire paying customers",
        budget=1000,
        max_cac=25,
        allowed_channels=["REDDIT"],
        constraints=["SECRET constraint"],
        known_audience=["SECRET audience"],
        known_competitors=["SECRET competitor"],
        reference_links=["https://example.com/?provider_token=SECRET_TOKEN"],
        assumptions=["SECRET assumption"],
        contradictions=[],
        status=ProductProfileStatus.CONFIRMED,
    )
    store.put(
        PRODUCT_INTAKE_NAMESPACE,
        str(PRODUCT_ID),
        {
            "product": product.model_dump(mode="json"),
            "brief": product.input_brief,
            "reference_links": list(product.reference_links),
            "questions": [],
            "answers": [],
            "answered_fields": [],
        },
    )

    opportunities = [
        DistributionOpportunityView(
            id=OPPORTUNITY_A,
            icp_id=ICP_ID,
            platform=DistributionPlatform.REDDIT,
            kind=OpportunityKind.SUBREDDIT,
            canonical_key="reddit:secret-community-a",
            title="Private-looking title A",
            url="https://reddit.com/r/example-a",
            relevance_score=80,
            rationale="Relevant public discussion",
            metadata={"provider_token": "SECRET_TOKEN"},
            evidence=[{"query": "public query", "url": "https://example.com/a"}],
        ),
        DistributionOpportunityView(
            id=OPPORTUNITY_B,
            icp_id=ICP_ID,
            platform=DistributionPlatform.REDDIT,
            kind=OpportunityKind.SUBREDDIT,
            canonical_key="reddit:secret-community-b",
            title="Private-looking title B",
            url="https://reddit.com/r/example-b",
            relevance_score=75,
            rationale="Another relevant public discussion",
            metadata={"provider_token": "SECRET_TOKEN"},
            evidence=[{"query": "public query", "url": "https://example.com/b"}],
        ),
    ]
    for opportunity in opportunities:
        store.put(
            AUDIENCE_OPPORTUNITY_NAMESPACE,
            str(opportunity.id),
            opportunity.model_dump(mode="json"),
        )

    plays = [
        _play(PLAY_A, OPPORTUNITY_A, "reddit_comment_a", 82),
        _play(PLAY_B, OPPORTUNITY_B, "reddit_comment_b", 78),
    ]
    generation = DistributionPlayGenerationResponse(
        product_id=PRODUCT_ID,
        play_count=2,
        ready_count=2,
        blocked_count=0,
        plays=plays,
    )
    store.put(
        DISTRIBUTION_PLAY_NAMESPACE,
        str(PRODUCT_ID),
        generation.model_dump(mode="json"),
    )

    experiments = [
        _experiment(EXPERIMENT_A, PLAY_A, OPPORTUNITY_A, ACTION_A),
        _experiment(EXPERIMENT_B, PLAY_B, OPPORTUNITY_B, ACTION_B),
    ]
    for experiment in experiments:
        store.put(
            DISTRIBUTION_EXPERIMENT_NAMESPACE,
            str(experiment.id),
            experiment.model_dump(mode="json"),
        )

    _seed_economics(store, EXPERIMENT_A, spend=100, paid_users=10, revenue=500)
    _seed_economics(store, EXPERIMENT_B, spend=100, paid_users=5, revenue=200)
    return store


def _play(
    play_id: UUID,
    opportunity_id: UUID,
    tactic_id: str,
    priority_score: float,
) -> DistributionPlayView:
    return DistributionPlayView(
        id=play_id,
        product_id=PRODUCT_ID,
        icp_id=ICP_ID,
        opportunity_id=opportunity_id,
        platform=DistributionPlatform.REDDIT,
        opportunity_kind=OpportunityKind.SUBREDDIT,
        opportunity_title="Not exported",
        tactic_id=tactic_id,
        tactic_class=DistributionTacticClass.COMMUNITY,
        action_type=DistributionActionType.COMMENT,
        automation_level=AutomationLevel.ASSISTED,
        attribution_level=AttributionLevel.ACTION,
        identity_required=False,
        selected_identity_id=None,
        community_policy_required=True,
        status=DistributionPlayStatus.READY,
        blockers=[],
        hypothesis="Test a useful community participation hypothesis for this segment.",
        execution_steps=["Read the thread", "Reply only when relevant"],
        success_metric="Paid users",
        estimated_cost_min=0,
        estimated_cost_max=0,
        effort_hours=1,
        time_to_signal_days=3,
        priority_score=priority_score,
        rationale=["Evidence-backed candidate"],
    )


def _experiment(
    experiment_id: UUID,
    play_id: UUID,
    opportunity_id: UUID,
    action_id: UUID,
) -> DistributionExperimentView:
    return DistributionExperimentView(
        id=experiment_id,
        product_id=PRODUCT_ID,
        distribution_play_id=play_id,
        opportunity_id=opportunity_id,
        action_id=action_id,
        status=DistributionExperimentStatus.FINISHED,
        attribution_level=AttributionLevel.ACTION,
        tracking_url=f"https://example.com/track/{experiment_id}",
        referral_token=experiment_id.hex[:16],
    )


def _seed_economics(
    store: MemoryRuntimeStateStore,
    experiment_id: UUID,
    *,
    spend: float,
    paid_users: int,
    revenue: float,
) -> None:
    spend_id = f"spend-{experiment_id}"
    store.put(
        DISTRIBUTION_SPEND_NAMESPACE,
        spend_id,
        {
            "spend_id": spend_id,
            "experiment_id": str(experiment_id),
            "amount": spend,
            "occurred_at": datetime.now(UTC).isoformat(),
            "properties": {"provider_secret": "SECRET_TOKEN"},
        },
    )
    for index in range(100):
        event_id = f"visit-{experiment_id}-{index}"
        store.put(
            DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE,
            event_id,
            {
                "event_id": event_id,
                "experiment_id": str(experiment_id),
                "event_type": "VISIT",
                "actor_id": None,
                "revenue": 0,
                "occurred_at": datetime.now(UTC).isoformat(),
                "properties": {"raw_secret": "SECRET_TOKEN"},
                "attributed_by": "experiment_id",
            },
        )
    for index in range(paid_users):
        actor_id = f"buyer-{experiment_id}-{index}"
        event_id = f"paid-{experiment_id}-{index}"
        store.put(
            DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE,
            event_id,
            {
                "event_id": event_id,
                "experiment_id": str(experiment_id),
                "event_type": "PAID",
                "actor_id": actor_id,
                "revenue": revenue / paid_users,
                "occurred_at": datetime.now(UTC).isoformat(),
                "properties": {"customer_email": "SECRET@example.com"},
                "attributed_by": "experiment_id",
            },
        )


def test_benchmark_is_reproducible_versioned_and_redacts_raw_sensitive_fields() -> None:
    store = _seed_store()
    builder = SelfResearchBenchmarkBuilder(store)

    first = builder.build()
    second = builder.build()

    assert first.dataset_version == second.dataset_version
    assert first.created_at == second.created_at
    assert first.case_count == 1
    assert first.decision_grade_count == 1
    case = first.cases[0]
    assert case.winner_objective == "CAC"
    assert case.winner_metric_value == 10
    assert case.measured_candidate_count == 2
    assert len(case.candidates) == 2
    assert all(item.provenance_present for item in case.candidates)

    serialized = json.dumps(first.model_dump(mode="json"), sort_keys=True)
    assert "SECRET" not in serialized
    assert "provider_token" not in serialized
    assert "reference_links" not in serialized
    assert "input_brief" not in serialized
    assert "customer_email" not in serialized


def test_fixed_evaluator_is_deterministic_and_unknown_top_choice_is_unsafe() -> None:
    store = _seed_store()
    builder = SelfResearchBenchmarkBuilder(store)
    dataset = builder.build()
    case = dataset.cases[0]
    winner = case.winner_candidate_key
    assert winner is not None
    evaluator = SelfResearchEvaluator(store=store, benchmark_builder=builder)

    good_payload = SelfResearchEvaluationInput(
        candidate_name="baseline-ranker",
        dataset_version=dataset.dataset_version,
        split=case.split,
        predictions=[
            SelfResearchCasePrediction(
                case_id=case.case_id,
                ranked_candidate_keys=[winner],
            )
        ],
    )
    first = evaluator.evaluate(good_payload)
    second = evaluator.evaluate(good_payload)
    assert first == second
    assert first.metrics.hit_at_1 == 1
    assert first.metrics.safety_violation_rate == 0
    assert first.metrics.provenance_coverage == 1

    unsafe = evaluator.evaluate(
        SelfResearchEvaluationInput(
            candidate_name="hallucinating-ranker",
            dataset_version=dataset.dataset_version,
            split=case.split,
            predictions=[
                SelfResearchCasePrediction(
                    case_id=case.case_id,
                    ranked_candidate_keys=["does-not-exist"],
                )
            ],
        )
    )
    assert unsafe.metrics.hit_at_1 == 0
    assert unsafe.metrics.unknown_recommendation_rate == 1
    assert unsafe.metrics.safety_violation_rate == 1
    assert unsafe.metrics.mean_normalized_regret == 1
    assert len(evaluator.list_evaluations()) == 2


def test_safety_regression_vetoes_candidate_even_with_higher_headline_score() -> None:
    baseline = _evaluation(
        "baseline",
        headline_score=0.50,
        safety_violation_rate=0,
        executable_rate=1,
        provenance=1,
        unknown_rate=0,
    )
    candidate = _evaluation(
        "candidate",
        headline_score=0.70,
        safety_violation_rate=0.10,
        executable_rate=0.90,
        provenance=1,
        unknown_rate=0,
    )
    evaluator = SelfResearchEvaluator(store=MemoryRuntimeStateStore())

    comparison = evaluator.compare(baseline, candidate)

    assert comparison.outcome == SelfResearchComparisonOutcome.VETO
    assert comparison.headline_delta == 0.2
    assert any("Safety violation rate" in reason for reason in comparison.reasons)


def _evaluation(
    name: str,
    *,
    headline_score: float,
    safety_violation_rate: float,
    executable_rate: float,
    provenance: float,
    unknown_rate: float,
) -> SelfResearchEvaluationView:
    return SelfResearchEvaluationView(
        evaluation_id=(name * 32)[:32],
        evaluator_version="1",
        candidate_name=name,
        dataset_version="d" * 32,
        split="TEST",
        metrics=SelfResearchEvaluationMetrics(
            evaluated_cases=10,
            hit_at_1=0.5,
            hit_at_3=0.8,
            mean_normalized_regret=0.2,
            executable_recommendation_rate=executable_rate,
            provenance_coverage=provenance,
            unknown_recommendation_rate=unknown_rate,
            safety_violation_rate=safety_violation_rate,
            complexity_penalty=0,
            headline_score=headline_score,
        ),
        created_at=datetime.now(UTC),
    )


def test_self_research_policy_protects_harness_and_limits_initial_edit_surface() -> None:
    assert is_self_research_path_protected("app/self_research_evaluator.py")
    assert is_self_research_path_protected(".github/workflows/deploy-production.yml")
    assert not is_self_research_path_editable("app/self_research_evaluator.py")
    assert is_self_research_path_editable("app/distribution_play_planner.py")
    assert not is_self_research_path_editable("app/autonomy_service.py")
