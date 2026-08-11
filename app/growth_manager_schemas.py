from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.analytics_schemas import ExperimentMetricsView


class NextHypothesisView(BaseModel):
    title: str
    change: str
    success_condition: str


class GrowthDecisionView(BaseModel):
    id: UUID
    product_id: UUID
    experiment_id: UUID
    action: Literal["SCALE", "CONTINUE", "MODIFY", "STOP"]
    rationale: list[str] = Field(min_length=1)
    policy_version: str
    metrics: ExperimentMetricsView
    budget_remaining: float | None = Field(default=None, ge=0)
    recommended_budget_increment: float = Field(ge=0)
    next_hypothesis: NextHypothesisView
    created_at: datetime
    duplicate: bool = False


class DecisionHistoryView(BaseModel):
    experiment_id: UUID
    decisions: list[GrowthDecisionView]


class ProductDecisionHistoryView(BaseModel):
    product_id: UUID
    decisions: list[GrowthDecisionView]


class LearningMemoryEntryView(BaseModel):
    id: UUID
    product_id: UUID
    experiment_id: UUID
    source_type: str
    template_id: str
    action: Literal["SCALE", "CONTINUE", "MODIFY", "STOP"]
    observed_cac: float | None = Field(default=None, ge=0)
    paid_users: int = Field(ge=0)
    revenue: float = Field(ge=0)
    summary: str
    created_at: datetime


class LearningMemoryView(BaseModel):
    product_id: UUID
    entries: list[LearningMemoryEntryView]
