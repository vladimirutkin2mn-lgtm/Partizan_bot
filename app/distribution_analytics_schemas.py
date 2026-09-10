from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from app.analytics_schemas import ExperimentMetricsView
from app.channel_execution import PublisherMode
from app.distribution_execution_schemas import DistributionExperimentView
from app.distribution_play_schemas import DistributionPlayView
from app.distribution_schemas import DistributionActionView
from app.distribution_types import DistributionActionType, DistributionPlatform


class DistributionCostCategory(StrEnum):
    RESEARCH_FEE = "RESEARCH_FEE"
    EXECUTION_FEE = "EXECUTION_FEE"
    DISTRIBUTION_SPEND = "DISTRIBUTION_SPEND"
    OPERATING_COST = "OPERATING_COST"


class DistributionEvidenceKind(StrEnum):
    OBSERVED = "OBSERVED"
    ESTIMATE = "ESTIMATE"
    SYNTHETIC = "SYNTHETIC"


DistributionEventType = Literal[
    "VISIT",
    "SIGNUP",
    "ACTIVATED",
    "PAID",
    "REPLY",
    "REMOVED",
]


class DistributionAnalyticsEventCreate(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    event_type: DistributionEventType
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
        if self.event_type == "REPLY":
            count = self.properties.get("count")
            if count is not None and (not isinstance(count, int) or isinstance(count, bool) or count < 0):
                raise ValueError("REPLY properties.count must be a non-negative integer")
        return self


class DistributionAnalyticsEventReceipt(BaseModel):
    event_id: UUID
    experiment_id: UUID
    event_type: DistributionEventType
    attributed_by: str
    duplicate: bool = False


class DistributionAnalyticsEventVerification(BaseModel):
    valid: Literal[True] = True
    persisted: Literal[False] = False
    event_id: UUID
    experiment_id: UUID
    event_type: DistributionEventType
    attributed_by: str
    duplicate: bool = False
    detail: str = "Event is valid and was not persisted"


class DistributionSpendCreate(BaseModel):
    spend_id: UUID = Field(default_factory=uuid4)
    amount: float = Field(gt=0)
    category: DistributionCostCategory = DistributionCostCategory.DISTRIBUTION_SPEND
    evidence_kind: DistributionEvidenceKind = DistributionEvidenceKind.OBSERVED
    publisher_mode: PublisherMode | None = None
    action_type: DistributionActionType | None = None
    occurred_at: datetime | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class DistributionSpendReceipt(BaseModel):
    spend_id: UUID
    experiment_id: UUID
    amount: float
    category: DistributionCostCategory = DistributionCostCategory.DISTRIBUTION_SPEND
    evidence_kind: DistributionEvidenceKind = DistributionEvidenceKind.OBSERVED
    duplicate: bool = False


class DistributionCostBreakdownView(BaseModel):
    research_fee: float = Field(default=0, ge=0)
    execution_fee: float = Field(default=0, ge=0)
    distribution_spend: float = Field(default=0, ge=0)
    operating_cost: float = Field(default=0, ge=0)
    customer_total: float = Field(default=0, ge=0)


class CustomerDistributionCostBreakdownView(BaseModel):
    research_fee: float = Field(default=0, ge=0)
    execution_fee: float = Field(default=0, ge=0)
    distribution_spend: float = Field(default=0, ge=0)
    total: float = Field(default=0, ge=0)


class DistributionExperimentAnalyticsView(BaseModel):
    experiment: DistributionExperimentView
    action: DistributionActionView
    play: DistributionPlayView
    event_count: int = Field(ge=0)
    metrics: ExperimentMetricsView
    publisher_mode: PublisherMode = PublisherMode.MANUAL
    replies: int = Field(default=0, ge=0)
    removals: int = Field(default=0, ge=0)
    costs: DistributionCostBreakdownView = Field(default_factory=DistributionCostBreakdownView)


class DistributionSliceMetricsView(BaseModel):
    dimension: Literal[
        "PLATFORM",
        "TACTIC",
        "IDENTITY",
        "PUBLISHER_MODE",
        "ACTION_TYPE",
        "OPPORTUNITY",
    ]
    key: str
    label: str
    experiment_count: int = Field(ge=0)
    spend: float = Field(ge=0)
    paid_users: int = Field(ge=0)
    revenue: float = Field(ge=0)
    cac: float | None = Field(default=None, ge=0)
    roas: float | None = Field(default=None, ge=0)
    replies: int = Field(default=0, ge=0)
    removals: int = Field(default=0, ge=0)


class DistributionProductAnalyticsView(BaseModel):
    product_id: UUID
    experiment_count: int = Field(ge=0)
    total_spend: float = Field(ge=0)
    total_paid_users: int = Field(ge=0)
    total_revenue: float = Field(ge=0)
    blended_cac: float | None = Field(default=None, ge=0)
    blended_roas: float | None = Field(default=None, ge=0)
    total_costs: DistributionCostBreakdownView = Field(default_factory=DistributionCostBreakdownView)
    experiments: list[DistributionExperimentAnalyticsView]
    breakdowns: list[DistributionSliceMetricsView]


class DistributionPricingAssumptionView(BaseModel):
    platform: DistributionPlatform
    action_type: DistributionActionType
    publisher_mode: PublisherMode
    observed_operating_cost: float = Field(ge=0)
    sample_count: int = Field(ge=1)
    updated_at: datetime


class CustomerDistributionEconomicsView(BaseModel):
    product_id: UUID
    experiment_count: int = Field(ge=0)
    costs: CustomerDistributionCostBreakdownView
    paid_users: int = Field(ge=0)
    revenue: float = Field(ge=0)
    cac: float | None = Field(default=None, ge=0)
    roas: float | None = Field(default=None, ge=0)


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
    publisher_mode: PublisherMode = PublisherMode.MANUAL
    action_type: DistributionActionType | None = None
    replies: int = Field(default=0, ge=0)
    removals: int = Field(default=0, ge=0)
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
    publisher_mode: PublisherMode = PublisherMode.MANUAL
    action_type: DistributionActionType | None = None
    action: Literal["SCALE", "CONTINUE", "MODIFY", "STOP"]
    observed_cac: float | None = Field(default=None, ge=0)
    paid_users: int = Field(ge=0)
    revenue: float = Field(ge=0)
    replies: int = Field(default=0, ge=0)
    removals: int = Field(default=0, ge=0)
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
