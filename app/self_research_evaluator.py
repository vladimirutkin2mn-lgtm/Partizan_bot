from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.self_research_benchmark import SelfResearchBenchmarkBuilder, self_research_benchmark_builder
from app.self_research_benchmark_schemas import (
    SelfResearchBenchmarkCase,
    SelfResearchCandidatePlay,
    SelfResearchComparisonOutcome,
    SelfResearchComparisonView,
    SelfResearchEvaluationInput,
    SelfResearchEvaluationMetrics,
    SelfResearchEvaluationView,
)

SELF_RESEARCH_EVALUATION_NAMESPACE = "self_research_evaluation"
SELF_RESEARCH_EVALUATOR_VERSION = "1"


class SelfResearchEvaluator:
    """Fixed, deterministic evaluator for offline Partizan self-research candidates."""

    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        benchmark_builder: SelfResearchBenchmarkBuilder | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._benchmark_builder = benchmark_builder or self_research_benchmark_builder

    def evaluate(self, payload: SelfResearchEvaluationInput) -> SelfResearchEvaluationView:
        dataset = self._benchmark_builder.get(payload.dataset_version)
        cases = [
            case
            for case in dataset.cases
            if case.split == payload.split and case.decision_grade
        ]
        predictions = {item.case_id: item for item in payload.predictions}
        hit_1 = 0
        hit_3 = 0
        regret_total = 0.0
        executable = 0
        provenance = 0
        unknown = 0
        safety_violations = 0
        complexity_total = 0.0

        for case in cases:
            prediction = predictions.get(case.case_id)
            ranked = list(prediction.ranked_candidate_keys) if prediction is not None else []
            top = ranked[0] if ranked else None
            known = {item.candidate_key: item for item in case.candidates}
            top_candidate = known.get(top) if top is not None else None

            if top == case.winner_candidate_key:
                hit_1 += 1
            if case.winner_candidate_key in ranked[:3]:
                hit_3 += 1
            regret_total += self._normalized_regret(case, top_candidate)

            if top_candidate is not None and top_candidate.executable:
                executable += 1
            else:
                safety_violations += 1
            if top_candidate is not None and top_candidate.provenance_present:
                provenance += 1
            if top is None or top_candidate is None:
                unknown += 1

            complexity_total += max(0, len(ranked) - 3) / max(1, len(case.candidates))

        count = len(cases)
        metrics = SelfResearchEvaluationMetrics(
            evaluated_cases=count,
            hit_at_1=self._ratio(hit_1, count),
            hit_at_3=self._ratio(hit_3, count),
            mean_normalized_regret=(round(regret_total / count, 6) if count else 0.0),
            executable_recommendation_rate=self._ratio(executable, count),
            provenance_coverage=self._ratio(provenance, count),
            unknown_recommendation_rate=self._ratio(unknown, count),
            safety_violation_rate=self._ratio(safety_violations, count),
            complexity_penalty=(round(complexity_total / count, 6) if count else 0.0),
            headline_score=0.0,
        )
        metrics = metrics.model_copy(update={"headline_score": self._headline(metrics)})
        evaluation_id = self._evaluation_id(payload)
        existing = self._store.get(SELF_RESEARCH_EVALUATION_NAMESPACE, evaluation_id)
        if existing is not None:
            return SelfResearchEvaluationView.model_validate(existing)
        result = SelfResearchEvaluationView(
            evaluation_id=evaluation_id,
            evaluator_version=SELF_RESEARCH_EVALUATOR_VERSION,
            candidate_name=payload.candidate_name,
            dataset_version=payload.dataset_version,
            split=payload.split,
            metrics=metrics,
            created_at=datetime.now(UTC),
        )
        self._store.put_if_absent(
            SELF_RESEARCH_EVALUATION_NAMESPACE,
            evaluation_id,
            result.model_dump(mode="json"),
        )
        return SelfResearchEvaluationView.model_validate(
            self._store.get(SELF_RESEARCH_EVALUATION_NAMESPACE, evaluation_id)
        )

    def compare(
        self,
        baseline: SelfResearchEvaluationView,
        candidate: SelfResearchEvaluationView,
    ) -> SelfResearchComparisonView:
        if baseline.dataset_version != candidate.dataset_version:
            raise ValueError("Self-research comparison requires the same dataset version")
        if baseline.split != candidate.split:
            raise ValueError("Self-research comparison requires the same benchmark split")
        if baseline.evaluator_version != candidate.evaluator_version:
            raise ValueError("Self-research comparison requires the same evaluator version")

        reasons: list[str] = []
        b = baseline.metrics
        c = candidate.metrics
        if c.safety_violation_rate > b.safety_violation_rate + 0.01:
            reasons.append("Safety violation rate regressed beyond the allowed 1pp tolerance.")
        if c.unknown_recommendation_rate > b.unknown_recommendation_rate + 0.01:
            reasons.append("Unknown recommendation rate regressed beyond the allowed 1pp tolerance.")
        if c.executable_recommendation_rate + 0.02 < b.executable_recommendation_rate:
            reasons.append("Executable recommendation rate regressed by more than 2pp.")
        if c.provenance_coverage + 0.05 < b.provenance_coverage:
            reasons.append("Evidence/provenance coverage regressed by more than 5pp.")

        delta = round(c.headline_score - b.headline_score, 6)
        if reasons:
            outcome = SelfResearchComparisonOutcome.VETO
        elif delta > 0:
            outcome = SelfResearchComparisonOutcome.KEEP
            reasons.append(f"Headline score improved by {delta:.6f} without a veto regression.")
        else:
            outcome = SelfResearchComparisonOutcome.DISCARD
            reasons.append(
                "Candidate did not improve the fixed headline score after safety/reliability gates."
            )
        return SelfResearchComparisonView(
            baseline_evaluation_id=baseline.evaluation_id,
            candidate_evaluation_id=candidate.evaluation_id,
            outcome=outcome,
            headline_delta=delta,
            reasons=reasons,
        )

    def list_evaluations(self) -> list[SelfResearchEvaluationView]:
        results = [
            SelfResearchEvaluationView.model_validate(payload)
            for payload in self._store.list_namespace(SELF_RESEARCH_EVALUATION_NAMESPACE)
        ]
        return sorted(results, key=lambda item: item.evaluation_id)

    def _normalized_regret(
        self,
        case: SelfResearchBenchmarkCase,
        candidate: SelfResearchCandidatePlay | None,
    ) -> float:
        if candidate is None or case.winner_metric_value is None or case.winner_objective is None:
            return 1.0
        actual = self._objective_value(candidate, case.winner_objective)
        if actual is None:
            return 1.0
        winner = case.winner_metric_value
        denominator = max(abs(winner), 1e-9)
        if case.winner_objective == "CAC":
            return round(max(0.0, actual - winner) / denominator, 6)
        return round(max(0.0, winner - actual) / denominator, 6)

    @staticmethod
    def _objective_value(candidate: SelfResearchCandidatePlay, objective: str) -> float | None:
        observed = candidate.observed
        if objective == "CAC":
            return observed.cac
        if objective == "PAID_USERS":
            return float(observed.paid_users)
        if objective == "ACTIVATION_RATE":
            if observed.signups == 0:
                return None
            return observed.activated_users / observed.signups
        if objective == "SIGNUP_RATE":
            if observed.visits == 0:
                return None
            return observed.signups / observed.visits
        raise ValueError(f"Unsupported benchmark objective: {objective}")

    @staticmethod
    def _headline(metrics: SelfResearchEvaluationMetrics) -> float:
        regret_quality = 1 - min(metrics.mean_normalized_regret, 1.0)
        score = (
            0.45 * metrics.hit_at_1
            + 0.25 * metrics.hit_at_3
            + 0.15 * metrics.executable_recommendation_rate
            + 0.10 * metrics.provenance_coverage
            + 0.05 * regret_quality
            - 0.40 * metrics.safety_violation_rate
            - 0.10 * metrics.complexity_penalty
        )
        return round(score, 6)

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 6) if denominator else 0.0

    @staticmethod
    def _evaluation_id(payload: SelfResearchEvaluationInput) -> str:
        canonical = json.dumps(
            {
                "evaluator_version": SELF_RESEARCH_EVALUATOR_VERSION,
                "payload": payload.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


self_research_evaluator = SelfResearchEvaluator()
