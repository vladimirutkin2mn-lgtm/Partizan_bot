from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SelfResearchSplit(StrEnum):
    TRAIN = "TRAIN"
    DEV = "DEV"
    TEST = "TEST"


class SelfResearchProductFacts(BaseModel):
    market: str | None = Field(default=None, max_length=200)
    language: str | None = Field(default=None, max_length=80)
    goal: str | None = Field(default=None, max_length=300)
    budget: float | None = Field(default=None, ge=0)
    max_cac: float | None = Field(default=None, ge=0)


class SelfResearchObservedEconomics(BaseModel):
    experiment_count: int = Field(default=0, ge=0)
    spend: float = Field(default=0, ge=0)
    visits: int = Field(default=0, ge=0)
    signups: int = Field(default=0, ge=0)
    activated_users: int = Field(default=0, ge=0)
    paid_users: int = Field(default=0, ge=0)
    revenue: float = Field(default=0, ge=0)
    cac: float | None = Field(default=None, ge=0)
    roas: float | None = Field(default=None, ge=0)


class SelfResearchCandidatePlay(BaseModel):
    candidate_key: str = Field(min_length=1, max_length=500)
    platform: str = Field(min_length=1, max_length=80)
    tactic_id: str = Field(min_length=1, max_length=200)
    tactic_class: str = Field(min_length=1, max_length=80)
    action_type: str = Field(min_length=1, max_length=80)
    opportunity_kind: str = Field(min_length=1, max_length=80)
    priority_score: float = Field(ge=0, le=100)
    executable: bool
    evidence_count: int = Field(default=0, ge=0)
    provenance_present: bool
    observed: SelfResearchObservedEconomics = Field(
        default_factory=SelfResearchObservedEconomics
    )


class SelfResearchBenchmarkCase(BaseModel):
    case_id: str = Field(min_length=16, max_length=64)
    split: SelfResearchSplit
    product: SelfResearchProductFacts
    candidates: list[SelfResearchCandidatePlay] = Field(min_length=1)
    winner_candidate_key: str | None = None
    winner_objective: str | None = None
    winner_metric_value: float | None = None
    measured_candidate_count: int = Field(default=0, ge=0)
    decision_grade: bool = False

    @model_validator(mode="after")
    def validate_winner(self) -> "SelfResearchBenchmarkCase":
        candidate_keys = {item.candidate_key for item in self.candidates}
        if self.winner_candidate_key is not None:
            if self.winner_candidate_key not in candidate_keys:
                raise ValueError("winner_candidate_key must reference one candidate")
            if not self.decision_grade:
                raise ValueError("winner requires a decision-grade case")
        if self.decision_grade and self.winner_candidate_key is None:
            raise ValueError("decision-grade case requires a winner")
        return self


class SelfResearchBenchmarkDataset(BaseModel):
    schema_version: Literal[1] = 1
    dataset_version: str = Field(min_length=16, max_length=64)
    case_count: int = Field(ge=0)
    train_count: int = Field(ge=0)
    dev_count: int = Field(ge=0)
    test_count: int = Field(ge=0)
    decision_grade_count: int = Field(ge=0)
    cases: list[SelfResearchBenchmarkCase] = Field(default_factory=list)
    created_at: datetime

    @model_validator(mode="after")
    def validate_counts(self) -> "SelfResearchBenchmarkDataset":
        if self.case_count != len(self.cases):
            raise ValueError("case_count must match cases length")
        counts = {
            SelfResearchSplit.TRAIN: self.train_count,
            SelfResearchSplit.DEV: self.dev_count,
            SelfResearchSplit.TEST: self.test_count,
        }
        for split, expected in counts.items():
            actual = sum(item.split == split for item in self.cases)
            if actual != expected:
                raise ValueError(f"{split.value.lower()}_count does not match cases")
        actual_decision_grade = sum(item.decision_grade for item in self.cases)
        if actual_decision_grade != self.decision_grade_count:
            raise ValueError("decision_grade_count does not match cases")
        return self


class SelfResearchCasePrediction(BaseModel):
    case_id: str = Field(min_length=16, max_length=64)
    ranked_candidate_keys: list[str] = Field(default_factory=list, max_length=100)


class SelfResearchEvaluationInput(BaseModel):
    candidate_name: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(min_length=16, max_length=64)
    split: SelfResearchSplit = SelfResearchSplit.TEST
    predictions: list[SelfResearchCasePrediction] = Field(default_factory=list)


class SelfResearchEvaluationMetrics(BaseModel):
    evaluated_cases: int = Field(ge=0)
    hit_at_1: float = Field(ge=0, le=1)
    hit_at_3: float = Field(ge=0, le=1)
    mean_normalized_regret: float = Field(ge=0)
    executable_recommendation_rate: float = Field(ge=0, le=1)
    provenance_coverage: float = Field(ge=0, le=1)
    unknown_recommendation_rate: float = Field(ge=0, le=1)
    safety_violation_rate: float = Field(ge=0, le=1)
    complexity_penalty: float = Field(ge=0)
    headline_score: float


class SelfResearchEvaluationView(BaseModel):
    evaluation_id: str = Field(min_length=16, max_length=64)
    evaluator_version: str = Field(min_length=1, max_length=50)
    candidate_name: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(min_length=16, max_length=64)
    split: SelfResearchSplit
    metrics: SelfResearchEvaluationMetrics
    created_at: datetime


class SelfResearchComparisonOutcome(StrEnum):
    KEEP = "KEEP"
    DISCARD = "DISCARD"
    VETO = "VETO"


class SelfResearchComparisonView(BaseModel):
    baseline_evaluation_id: str
    candidate_evaluation_id: str
    outcome: SelfResearchComparisonOutcome
    headline_delta: float
    reasons: list[str] = Field(min_length=1)
