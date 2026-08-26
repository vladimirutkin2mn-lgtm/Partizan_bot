from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.runtime_store import MemoryRuntimeStateStore
from app.self_research_benchmark import (
    SELF_RESEARCH_BENCHMARK_NAMESPACE,
    SelfResearchBenchmarkBuilder,
)
from app.self_research_benchmark_schemas import (
    SelfResearchBenchmarkCase,
    SelfResearchBenchmarkDataset,
    SelfResearchCandidatePlay,
    SelfResearchObservedEconomics,
    SelfResearchProductFacts,
    SelfResearchSplit,
)
from app.self_research_evaluator import SelfResearchEvaluator
from app.self_research_loop import SelfResearchLoopService
from app.self_research_loop_schemas import (
    PlannerScoringSpec,
    SELF_RESEARCH_PLANNER_PATH,
    SelfResearchMutation,
    SelfResearchRunRequest,
)

DATASET_VERSION = "d" * 32
CASE_ID = "c" * 32
CANDIDATE_A = "a" * 24
CANDIDATE_B = "b" * 24


class StaticMutationProposer:
    def __init__(self, mutation: SelfResearchMutation) -> None:
        self._mutation = mutation

    def propose(self, baseline, *, tried_spec_fingerprints):
        return self._mutation


def _candidate(
    key: str,
    *,
    tactic_class: str,
    priority: float,
    cac: float,
    provenance: bool,
) -> SelfResearchCandidatePlay:
    return SelfResearchCandidatePlay(
        candidate_key=key,
        platform="REDDIT",
        tactic_id=f"tactic-{key[0]}",
        tactic_class=tactic_class,
        action_type="COMMENT",
        opportunity_kind="SUBREDDIT",
        priority_score=priority,
        executable=True,
        evidence_count=2 if provenance else 0,
        provenance_present=provenance,
        observed=SelfResearchObservedEconomics(
            experiment_count=1,
            spend=100,
            visits=100,
            signups=20,
            activated_users=10,
            paid_users=5,
            revenue=200,
            cac=cac,
            roas=2,
        ),
    )


def _seed_dataset(
    store: MemoryRuntimeStateStore,
    *,
    winner: str,
    winner_provenance: bool = True,
) -> SelfResearchBenchmarkDataset:
    candidate_a = _candidate(
        CANDIDATE_A,
        tactic_class="PAID_PLATFORM",
        priority=90,
        cac=10 if winner == CANDIDATE_A else 20,
        provenance=True,
    )
    candidate_b = _candidate(
        CANDIDATE_B,
        tactic_class="COMMUNITY",
        priority=80,
        cac=10 if winner == CANDIDATE_B else 20,
        provenance=winner_provenance,
    )
    dataset = SelfResearchBenchmarkDataset(
        dataset_version=DATASET_VERSION,
        case_count=1,
        train_count=0,
        dev_count=1,
        test_count=0,
        decision_grade_count=1,
        cases=[
            SelfResearchBenchmarkCase(
                case_id=CASE_ID,
                split=SelfResearchSplit.DEV,
                product=SelfResearchProductFacts(
                    market="US",
                    language="English",
                    goal="Acquire paying customers",
                    budget=1000,
                    max_cac=25,
                ),
                candidates=[candidate_a, candidate_b],
                winner_candidate_key=winner,
                winner_objective="CAC",
                winner_metric_value=10,
                measured_candidate_count=2,
                decision_grade=True,
            )
        ],
        created_at=datetime.now(UTC),
    )
    store.put(
        SELF_RESEARCH_BENCHMARK_NAMESPACE,
        DATASET_VERSION,
        dataset.model_dump(mode="json"),
    )
    return dataset


def _community_mutation(*, target_path: str = SELF_RESEARCH_PLANNER_PATH):
    baseline = PlannerScoringSpec()
    candidate = baseline.model_copy(update={"community_bonus": 20.0})
    return SelfResearchMutation(
        mutation_id="m" * 32,
        target_path=target_path,
        dimension="community_bonus",
        before_value=0,
        after_value=20,
        rationale="Test whether community plays deserve a bounded ranking preference.",
        candidate_spec=candidate,
    )


def _service(
    store: MemoryRuntimeStateStore,
    mutation: SelfResearchMutation,
) -> SelfResearchLoopService:
    builder = SelfResearchBenchmarkBuilder(store)
    evaluator = SelfResearchEvaluator(store=store, benchmark_builder=builder)
    return SelfResearchLoopService(
        store=store,
        benchmark_builder=builder,
        evaluator=evaluator,
        proposer=StaticMutationProposer(mutation),
    )


def _request() -> SelfResearchRunRequest:
    return SelfResearchRunRequest(
        dataset_version=DATASET_VERSION,
        split=SelfResearchSplit.DEV,
    )


def test_better_candidate_is_kept_only_as_persisted_research_champion() -> None:
    store = MemoryRuntimeStateStore()
    _seed_dataset(store, winner=CANDIDATE_B, winner_provenance=True)
    service = _service(store, _community_mutation())

    trial = service.run_once(_request())

    assert trial.status == "KEEP"
    assert trial.comparison_outcome == "KEEP"
    assert trial.baseline_evaluation is not None
    assert trial.candidate_evaluation is not None
    assert (
        trial.candidate_evaluation.metrics.headline_score
        > trial.baseline_evaluation.metrics.headline_score
    )
    champion = service.current_champion(DATASET_VERSION)
    assert champion is not None
    assert champion.spec.community_bonus == 20
    assert champion.source_trial_id == trial.trial_id
    assert champion.spec.target_path == SELF_RESEARCH_PLANNER_PATH


def test_worse_candidate_is_discarded_and_baseline_remains_active() -> None:
    store = MemoryRuntimeStateStore()
    _seed_dataset(store, winner=CANDIDATE_A, winner_provenance=True)
    service = _service(store, _community_mutation())

    trial = service.run_once(_request())

    assert trial.status == "DISCARD"
    assert trial.comparison_outcome == "DISCARD"
    champion = service.current_champion(DATASET_VERSION)
    assert champion is not None
    assert champion.spec == PlannerScoringSpec()
    assert champion.source_trial_id is None


def test_reliability_regression_vetoes_headline_improvement() -> None:
    store = MemoryRuntimeStateStore()
    _seed_dataset(store, winner=CANDIDATE_B, winner_provenance=False)
    service = _service(store, _community_mutation())

    trial = service.run_once(_request())

    assert trial.baseline_evaluation is not None
    assert trial.candidate_evaluation is not None
    assert (
        trial.candidate_evaluation.metrics.headline_score
        > trial.baseline_evaluation.metrics.headline_score
    )
    assert trial.status == "VETO"
    assert trial.comparison_outcome == "VETO"
    assert any("provenance" in reason.lower() for reason in trial.reasons)
    champion = service.current_champion(DATASET_VERSION)
    assert champion is not None
    assert champion.spec == PlannerScoringSpec()


def test_protected_target_is_blocked_before_candidate_evaluation() -> None:
    store = MemoryRuntimeStateStore()
    _seed_dataset(store, winner=CANDIDATE_B)
    mutation = _community_mutation(target_path="app/self_research_evaluator.py")
    service = _service(store, mutation)

    trial = service.run_once(_request())

    assert trial.status == "BLOCKED"
    assert trial.candidate_evaluation is None
    assert any("protected" in reason.lower() for reason in trial.reasons)
    champion = service.current_champion(DATASET_VERSION)
    assert champion is not None
    assert champion.spec == PlannerScoringSpec()


def test_multi_dimension_candidate_is_blocked_fail_closed() -> None:
    store = MemoryRuntimeStateStore()
    _seed_dataset(store, winner=CANDIDATE_B)
    baseline = PlannerScoringSpec()
    candidate = baseline.model_copy(
        update={"community_bonus": 20.0, "provenance_bonus": 5.0}
    )
    mutation = SelfResearchMutation(
        mutation_id="x" * 32,
        target_path=SELF_RESEARCH_PLANNER_PATH,
        dimension="community_bonus",
        before_value=0,
        after_value=20,
        rationale="Maliciously changes more than one dimension.",
        candidate_spec=candidate,
    )
    service = _service(store, mutation)

    trial = service.run_once(_request())

    assert trial.status == "BLOCKED"
    assert trial.candidate_evaluation is None
    assert any("exactly one" in reason.lower() for reason in trial.reasons)


def test_research_champion_and_history_survive_service_restart() -> None:
    store = MemoryRuntimeStateStore()
    _seed_dataset(store, winner=CANDIDATE_B)
    first_service = _service(store, _community_mutation())
    trial = first_service.run_once(_request())
    assert trial.status == "KEEP"

    restarted = _service(store, _community_mutation())
    history = restarted.history(DATASET_VERSION)

    assert history.champion is not None
    assert history.champion.spec.community_bonus == 20
    assert history.champion.source_trial_id == trial.trial_id
    assert len(history.trials) == 1
    assert history.trials[0].trial_id == trial.trial_id


def test_autonomous_loop_cannot_tune_on_test_holdout() -> None:
    with pytest.raises(ValidationError, match="TEST holdout"):
        SelfResearchRunRequest(
            dataset_version=DATASET_VERSION,
            split=SelfResearchSplit.TEST,
        )
