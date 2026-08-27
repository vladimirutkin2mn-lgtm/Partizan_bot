from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.self_research_benchmark_schemas import (
    SelfResearchBenchmarkCase,
    SelfResearchBenchmarkDataset,
    SelfResearchCandidatePlay,
    SelfResearchCasePrediction,
    SelfResearchSplit,
)
from app.self_research_loop_schemas import (
    SELF_RESEARCH_PLANNER_PATH,
    PlannerScoringSpec,
    SelfResearchMutation,
)


class SelfResearchPlannerRanker:
    """Apply a bounded research-only scoring spec to benchmark candidates.

    The ranker intentionally never reads observed economics. Those fields belong to
    evaluation ground truth, not to candidate generation.
    """

    def predictions(
        self,
        dataset: SelfResearchBenchmarkDataset,
        *,
        split: SelfResearchSplit,
        spec: PlannerScoringSpec,
    ) -> list[SelfResearchCasePrediction]:
        return [
            SelfResearchCasePrediction(
                case_id=case.case_id,
                ranked_candidate_keys=[
                    candidate.candidate_key
                    for candidate in self._rank(case, spec)[:3]
                ],
            )
            for case in dataset.cases
            if case.split == split and case.decision_grade
        ]

    def _rank(
        self,
        case: SelfResearchBenchmarkCase,
        spec: PlannerScoringSpec,
    ) -> list[SelfResearchCandidatePlay]:
        return sorted(
            case.candidates,
            key=lambda candidate: (
                not candidate.executable,
                -self._score(candidate, spec),
                candidate.candidate_key,
            ),
        )

    @staticmethod
    def _score(candidate: SelfResearchCandidatePlay, spec: PlannerScoringSpec) -> float:
        score = candidate.priority_score * spec.priority_weight
        if candidate.provenance_present:
            score += spec.provenance_bonus
        score += min(candidate.evidence_count, 20) * spec.evidence_weight
        if candidate.tactic_class == "COMMUNITY":
            score += spec.community_bonus
        elif candidate.tactic_class == "OWNED_ORGANIC":
            score += spec.owned_organic_bonus
        elif candidate.tactic_class == "PAID_PLATFORM":
            score += spec.paid_platform_bonus
        return round(score, 6)


@dataclass(frozen=True, slots=True)
class _MutationStep:
    dimension: str
    delta: float
    rationale: str


_MUTATION_STEPS = (
    _MutationStep(
        "provenance_bonus",
        5.0,
        "Test whether stronger evidence provenance should break otherwise-close planner rankings.",
    ),
    _MutationStep(
        "evidence_weight",
        0.5,
        "Test a small preference for opportunities supported by more independent evidence.",
    ),
    _MutationStep(
        "community_bonus",
        5.0,
        "Test a bounded ranking preference for community distribution plays.",
    ),
    _MutationStep(
        "owned_organic_bonus",
        5.0,
        "Test a bounded ranking preference for owned-organic distribution plays.",
    ),
    _MutationStep(
        "paid_platform_bonus",
        -5.0,
        "Test a bounded ranking penalty for paid-platform plays when other evidence is similar.",
    ),
    _MutationStep(
        "priority_weight",
        -0.1,
        "Test slightly less reliance on the current aggregate priority score.",
    ),
    _MutationStep(
        "priority_weight",
        0.1,
        "Test slightly more reliance on the current aggregate priority score.",
    ),
)


class SelfResearchMutationProposer:
    """Deterministically explore one reviewable planner-scoring dimension at a time."""

    def propose(
        self,
        baseline: PlannerScoringSpec,
        *,
        tried_spec_fingerprints: set[str],
    ) -> SelfResearchMutation | None:
        for step in _MUTATION_STEPS:
            before = float(getattr(baseline, step.dimension))
            after = round(before + step.delta, 6)
            try:
                candidate = baseline.model_copy(update={step.dimension: after})
                candidate = PlannerScoringSpec.model_validate(candidate.model_dump())
            except ValueError:
                continue
            fingerprint = self.spec_fingerprint(candidate)
            if fingerprint in tried_spec_fingerprints:
                continue
            mutation_id = self._mutation_id(
                baseline=baseline,
                dimension=step.dimension,
                after=after,
            )
            return SelfResearchMutation(
                mutation_id=mutation_id,
                target_path=SELF_RESEARCH_PLANNER_PATH,
                dimension=step.dimension,
                before_value=before,
                after_value=after,
                rationale=step.rationale,
                candidate_spec=candidate,
            )
        return None

    @staticmethod
    def spec_fingerprint(spec: PlannerScoringSpec) -> str:
        canonical = json.dumps(
            spec.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]

    def _mutation_id(
        self,
        *,
        baseline: PlannerScoringSpec,
        dimension: str,
        after: float,
    ) -> str:
        payload = {
            "baseline": self.spec_fingerprint(baseline),
            "dimension": dimension,
            "after": after,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
