from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.self_research_benchmark import SelfResearchBenchmarkBuilder
from app.self_research_benchmark_schemas import SelfResearchEvaluationInput
from app.self_research_evaluator import SelfResearchEvaluator
from app.self_research_loop_schemas import (
    PlannerScoringSpec,
    SelfResearchChampionView,
    SelfResearchHistoryView,
    SelfResearchMutation,
    SelfResearchRunRequest,
    SelfResearchTrialStatus,
    SelfResearchTrialView,
)
from app.self_research_planner_ranker import (
    SelfResearchMutationProposer,
    SelfResearchPlannerRanker,
)
from app.self_research_policy import (
    is_self_research_path_editable,
    is_self_research_path_protected,
)

SELF_RESEARCH_CHAMPION_NAMESPACE = "self_research_champion"
SELF_RESEARCH_TRIAL_NAMESPACE = "self_research_trial"


class SelfResearchLoopService:
    """Run one offline, bounded Partizan self-research iteration.

    Accepted candidates are persisted only as research champion specs. This service never
    modifies repository files, invokes providers, deploys code or merges a Git branch.
    """

    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        benchmark_builder: SelfResearchBenchmarkBuilder | None = None,
        evaluator: SelfResearchEvaluator | None = None,
        ranker: SelfResearchPlannerRanker | None = None,
        proposer: SelfResearchMutationProposer | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._benchmark_builder = benchmark_builder or SelfResearchBenchmarkBuilder(self._store)
        self._evaluator = evaluator or SelfResearchEvaluator(
            store=self._store,
            benchmark_builder=self._benchmark_builder,
        )
        self._ranker = ranker or SelfResearchPlannerRanker()
        self._proposer = proposer or SelfResearchMutationProposer()

    @property
    def store(self) -> RuntimeStateStore:
        return self._store

    def run_once(self, payload: SelfResearchRunRequest) -> SelfResearchTrialView:
        dataset = self._benchmark_builder.get(payload.dataset_version)
        champion = self._champion_or_baseline(dataset.dataset_version)
        baseline_evaluation = self._evaluate_spec(
            dataset=dataset,
            split=payload.split,
            spec=champion.spec,
            candidate_name=(
                "planner-baseline-"
                + SelfResearchMutationProposer.spec_fingerprint(champion.spec)
            ),
        )
        eligible_cases = baseline_evaluation.metrics.evaluated_cases
        if eligible_cases == 0:
            return self._record_trial(
                dataset_version=dataset.dataset_version,
                split=payload.split,
                baseline_spec=champion.spec,
                baseline_evaluation=baseline_evaluation,
                status=SelfResearchTrialStatus.BLOCKED,
                reasons=[
                    "Selected benchmark split has no decision-grade cases; self-research cannot "
                    "optimize against an empty evaluation surface."
                ],
            )

        tried = {
            SelfResearchMutationProposer.spec_fingerprint(trial.candidate_spec)
            for trial in self.history(dataset.dataset_version).trials
            if trial.candidate_spec is not None
        }
        mutation = self._proposer.propose(
            champion.spec,
            tried_spec_fingerprints=tried,
        )
        if mutation is None:
            return self._record_trial(
                dataset_version=dataset.dataset_version,
                split=payload.split,
                baseline_spec=champion.spec,
                baseline_evaluation=baseline_evaluation,
                status=SelfResearchTrialStatus.EXHAUSTED,
                reasons=[
                    "No unseen bounded planner-scoring mutation remains in the current research "
                    "proposal catalog."
                ],
            )

        validation_error = self._mutation_error(champion.spec, mutation)
        if validation_error is not None:
            return self._record_trial(
                dataset_version=dataset.dataset_version,
                split=payload.split,
                baseline_spec=champion.spec,
                mutation=mutation,
                candidate_spec=mutation.candidate_spec,
                baseline_evaluation=baseline_evaluation,
                status=SelfResearchTrialStatus.BLOCKED,
                reasons=[validation_error],
            )

        candidate_evaluation = self._evaluate_spec(
            dataset=dataset,
            split=payload.split,
            spec=mutation.candidate_spec,
            candidate_name="planner-candidate-" + mutation.mutation_id,
        )
        comparison = self._evaluator.compare(baseline_evaluation, candidate_evaluation)
        status = SelfResearchTrialStatus(comparison.outcome.value)
        trial = self._record_trial(
            dataset_version=dataset.dataset_version,
            split=payload.split,
            baseline_spec=champion.spec,
            mutation=mutation,
            candidate_spec=mutation.candidate_spec,
            baseline_evaluation=baseline_evaluation,
            candidate_evaluation=candidate_evaluation,
            comparison_outcome=comparison.outcome,
            status=status,
            reasons=comparison.reasons,
        )
        if status == SelfResearchTrialStatus.KEEP:
            self._persist_champion(
                SelfResearchChampionView(
                    dataset_version=dataset.dataset_version,
                    spec=mutation.candidate_spec,
                    source_trial_id=trial.trial_id,
                    promoted_at=datetime.now(UTC),
                )
            )
        return trial

    def history(self, dataset_version: str) -> SelfResearchHistoryView:
        trials = [
            SelfResearchTrialView.model_validate(item)
            for item in self._store.list_namespace(SELF_RESEARCH_TRIAL_NAMESPACE)
            if item.get("dataset_version") == dataset_version
        ]
        trials.sort(key=lambda item: (item.created_at, item.trial_id))
        return SelfResearchHistoryView(
            dataset_version=dataset_version,
            champion=self.current_champion(dataset_version),
            trials=trials,
        )

    def current_champion(self, dataset_version: str) -> SelfResearchChampionView | None:
        payload = self._store.get(SELF_RESEARCH_CHAMPION_NAMESPACE, dataset_version)
        if payload is None:
            return None
        return SelfResearchChampionView.model_validate(payload)

    def _champion_or_baseline(self, dataset_version: str) -> SelfResearchChampionView:
        current = self.current_champion(dataset_version)
        if current is not None:
            return current
        baseline = SelfResearchChampionView(
            dataset_version=dataset_version,
            spec=PlannerScoringSpec(),
            source_trial_id=None,
            promoted_at=datetime.now(UTC),
        )
        if self._store.put_if_absent(
            SELF_RESEARCH_CHAMPION_NAMESPACE,
            dataset_version,
            baseline.model_dump(mode="json"),
        ):
            return baseline
        return SelfResearchChampionView.model_validate(
            self._store.get(SELF_RESEARCH_CHAMPION_NAMESPACE, dataset_version)
        )

    def _evaluate_spec(self, *, dataset, split, spec, candidate_name):
        predictions = self._ranker.predictions(dataset, split=split, spec=spec)
        return self._evaluator.evaluate(
            SelfResearchEvaluationInput(
                candidate_name=candidate_name,
                dataset_version=dataset.dataset_version,
                split=split,
                predictions=predictions,
            )
        )

    def _mutation_error(
        self,
        baseline: PlannerScoringSpec,
        mutation: SelfResearchMutation,
    ) -> str | None:
        if is_self_research_path_protected(mutation.target_path):
            return f"Self-research target is protected and cannot be mutated: {mutation.target_path}"
        if not is_self_research_path_editable(mutation.target_path):
            return f"Self-research target is outside the editable allowlist: {mutation.target_path}"
        if mutation.candidate_spec.target_path != mutation.target_path:
            return "Candidate scoring spec target does not match the mutation target path."
        baseline_payload = baseline.model_dump(mode="json")
        candidate_payload = mutation.candidate_spec.model_dump(mode="json")
        changed = [
            key
            for key in baseline_payload
            if baseline_payload[key] != candidate_payload[key]
        ]
        if changed != [mutation.dimension]:
            return (
                "A self-research candidate must change exactly one declared scoring dimension; "
                f"changed={changed}."
            )
        if float(baseline_payload[mutation.dimension]) != mutation.before_value:
            return "Mutation before_value does not match the active research champion."
        return None

    def _persist_champion(self, champion: SelfResearchChampionView) -> None:
        self._store.put(
            SELF_RESEARCH_CHAMPION_NAMESPACE,
            champion.dataset_version,
            champion.model_dump(mode="json"),
        )

    def _record_trial(
        self,
        *,
        dataset_version,
        split,
        baseline_spec,
        status,
        reasons,
        mutation=None,
        candidate_spec=None,
        baseline_evaluation=None,
        candidate_evaluation=None,
        comparison_outcome=None,
    ) -> SelfResearchTrialView:
        trial = SelfResearchTrialView(
            trial_id=uuid4().hex,
            dataset_version=dataset_version,
            split=split,
            baseline_spec=baseline_spec,
            mutation=mutation,
            candidate_spec=candidate_spec,
            baseline_evaluation=baseline_evaluation,
            candidate_evaluation=candidate_evaluation,
            comparison_outcome=comparison_outcome,
            status=status,
            reasons=reasons,
            created_at=datetime.now(UTC),
        )
        if not self._store.put_if_absent(
            SELF_RESEARCH_TRIAL_NAMESPACE,
            trial.trial_id,
            trial.model_dump(mode="json"),
        ):
            raise RuntimeError("Self-research trial id collision")
        return trial


self_research_loop_service = SelfResearchLoopService()
