from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.self_research_benchmark_schemas import (
    SelfResearchComparisonOutcome,
    SelfResearchEvaluationView,
    SelfResearchSplit,
)

SELF_RESEARCH_PLANNER_PATH = "app/distribution_play_planner.py"


class PlannerScoringSpec(BaseModel):
    target_path: Literal["app/distribution_play_planner.py"] = SELF_RESEARCH_PLANNER_PATH
    priority_weight: float = Field(default=1.0, ge=0.5, le=1.5)
    provenance_bonus: float = Field(default=0.0, ge=-20, le=20)
    evidence_weight: float = Field(default=0.0, ge=-2, le=2)
    community_bonus: float = Field(default=0.0, ge=-20, le=20)
    owned_organic_bonus: float = Field(default=0.0, ge=-20, le=20)
    paid_platform_bonus: float = Field(default=0.0, ge=-20, le=20)


class SelfResearchMutation(BaseModel):
    mutation_id: str = Field(min_length=16, max_length=64)
    target_path: str = Field(min_length=1, max_length=300)
    dimension: str = Field(min_length=1, max_length=80)
    before_value: float
    after_value: float
    rationale: str = Field(min_length=1, max_length=1000)
    candidate_spec: PlannerScoringSpec

    @model_validator(mode="after")
    def validate_dimension(self) -> SelfResearchMutation:
        allowed = set(PlannerScoringSpec.model_fields) - {"target_path"}
        if self.dimension not in allowed:
            raise ValueError("Self-research mutation targets an unsupported scoring dimension")
        actual = float(getattr(self.candidate_spec, self.dimension))
        if actual != self.after_value:
            raise ValueError("Mutation after_value must match candidate_spec")
        if self.before_value == self.after_value:
            raise ValueError("Mutation must change one scoring dimension")
        return self


class SelfResearchTrialStatus(StrEnum):
    KEEP = "KEEP"
    DISCARD = "DISCARD"
    VETO = "VETO"
    BLOCKED = "BLOCKED"
    EXHAUSTED = "EXHAUSTED"


class SelfResearchTrialView(BaseModel):
    trial_id: str = Field(min_length=16, max_length=64)
    dataset_version: str = Field(min_length=16, max_length=64)
    split: SelfResearchSplit
    baseline_spec: PlannerScoringSpec
    mutation: SelfResearchMutation | None = None
    candidate_spec: PlannerScoringSpec | None = None
    baseline_evaluation: SelfResearchEvaluationView | None = None
    candidate_evaluation: SelfResearchEvaluationView | None = None
    comparison_outcome: SelfResearchComparisonOutcome | None = None
    status: SelfResearchTrialStatus
    reasons: list[str] = Field(min_length=1)
    created_at: datetime


class SelfResearchChampionView(BaseModel):
    dataset_version: str = Field(min_length=16, max_length=64)
    spec: PlannerScoringSpec
    source_trial_id: str | None = Field(default=None, min_length=16, max_length=64)
    promoted_at: datetime


class SelfResearchRunRequest(BaseModel):
    dataset_version: str = Field(min_length=16, max_length=64)
    split: SelfResearchSplit = SelfResearchSplit.DEV

    @model_validator(mode="after")
    def protect_holdout(self) -> SelfResearchRunRequest:
        if self.split == SelfResearchSplit.TEST:
            raise ValueError(
                "Autonomous self-research cannot tune on the TEST holdout split"
            )
        return self


class SelfResearchHistoryView(BaseModel):
    dataset_version: str = Field(min_length=16, max_length=64)
    champion: SelfResearchChampionView | None = None
    trials: list[SelfResearchTrialView] = Field(default_factory=list)
