from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from app.analytics_schemas import ExperimentMetricsView
from app.distribution_execution_schemas import DistributionExperimentView
from app.distribution_play_schemas import DistributionPlayView
from app.distribution_schemas import DistributionActionView
from app.distribution_types import DistributionPlatform


class DistributionAnalyticsEventCreate(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    event_type: Literal["VISIT", "SIGNUP", "ACTIVATED", "PAID"]
    experiment_id: UUID | None = None
    referral_token: str | None = Field(default=None, max_length=64)
    action_id: UUID | None = None
    actor_id: str | None = Field(default=None, max_length=200)
    revenue: float = Field(default=0, ge=0)
    occurred_at: datetime | None = None
    properties: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_event(self) -> "DistributionAnalyticsEventCreate":
        if not any((self.experiment_id, self.referral_token, self.action_id)):
            raise ValueError("At least one distribution attribution identifier is required")
        if self.event_type != "PAID" and self.revenue != 0:
            raise ValueError("revenue is only allowed for PAID events")
        return self


class DistributionAnalyticsEventReceipt(BaseModel):
    event_id: UUID
    experiment_id: UUID
    event_type: Literal["VISIT", "SIGNUP", "ACTIVATED", "PAID"]
    attributed_by: str
    duplicate: bool = False


class DistributionSpendCreate(BaseModel):
    spend_id: UUID = Field(default_factory=uuid4)
    amount: float = Field(gt=0)
    occurred_at: datetime | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class DistributionSpendReceipt(BaseModel):
    spend_id: UUID
    experiment_id: UUID
    amount: float
    duplicate: bool = False


class DistributionExperimentAnalyticsView(BaseModel):
    experiment: DistributionExperimentView
    action: DistributionActionView
    play: DistributionPlayView
    event_count: int = Field(ge=0)
    metrics: ExperimentMetricsView


class DistributionSliceMetricsView(BaseModel):
    dimension: Literal["PLATFORM", "TACTIC", "IDENTITY"]
    key: str
    label: str
    experiment_count: int = Field(ge=0)
    spend: float = Field(ge=0)
    paid_users: int = Field(ge=0)
    revenue: float = Field(ge=0)
    cac: float | None = Field(default=None, ge=0)
    roas: float | None = Field(default=None, ge=0)


class DistributionProductAnalyticsView(BaseModel):
    product_id: UUID
    experiment_count: int = Field(ge=0)
    total_spend: float = Field(ge=0)
    total_paid_users: int = Field(ge=0)
    total_revenue: float = Field(ge=0)
    blended_cac: float | None = Field(default=None, ge=0)
    blended_roas: float | None = Field(default=None, ge=0)
    experiments: list[DistributionExperimentAnalyticsView]
    breakdowns: list[DistributionSliceMetricsView]


class DistributionGrowthDecisionView(BaseModel):
    id: UUID
    product_id: UUID
    experiment_id: UUID
    action: Literal["SCALE", "CONTINUE", "MODIFY", "STOP"]
    rationale: list[str] = Field(min_length=1)
    metrics: ExperimentMetricsView
    platform: DistributionPlatform
    tactic_id: str
    opportunity_id: UUID
    distribution_identity_id: UUID | None = None
    budget_remaining: float | None = Field(default=None, ge=0)
    recommended_budget_increment: float = Field(ge=0)
    created_at: datetime
    duplicate: bool = False


class DistributionLearningEntryView(BaseModel):
    id: UUID
    product_id: UUID
    experiment_id: UUID
    platform: DistributionPlatform
    tactic_id: str
    opportunity_id: UUID
    distribution_identity_id: UUID | None = None
    action: Literal["SCALE", "CONTINUE", "MODIFY", "STOP"]
    observed_cac: float | None = Field(default=None, ge=0)
    paid_users: int = Field(ge=0)
    revenue: float = Field(ge=0)
    summary: str
    created_at: datetime


class DistributionLearningMemoryView(BaseModel):
    product_id: UUID
    entries: list[DistributionLearningEntryView]


class DistributionPortfolioItemView(BaseModel):
    play: DistributionPlayView
    portfolio_score: float = Field(ge=0, le=100)
    recommended_budget_cap: float = Field(ge=0)
    rationale: list[str] = Field(min_length=1)


class DistributionPortfolioView(BaseModel):
    product_id: UUID
    max_items: int = Field(ge=1, le=12)
    budget_remaining: float | None = Field(default=None, ge=0)
    items: list[DistributionPortfolioItemView]
