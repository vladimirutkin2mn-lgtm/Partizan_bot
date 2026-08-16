from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from app.schemas import ExperimentView


class AnalyticsEventCreate(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    event_type: Literal["VISIT", "SIGNUP", "ACTIVATED", "PAID"]
    experiment_id: UUID | None = None
    referral_token: str | None = Field(default=None, max_length=64)
    utm_content: UUID | None = None
    actor_id: str | None = Field(default=None, max_length=200)
    revenue: float = Field(default=0, ge=0)
    occurred_at: datetime | None = None
    properties: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_event(self) -> "AnalyticsEventCreate":
        if not any((self.experiment_id, self.referral_token, self.utm_content)):
            raise ValueError("At least one attribution identifier is required")
        if self.event_type != "PAID" and self.revenue != 0:
            raise ValueError("revenue is only allowed for PAID events")
        return self


class AnalyticsEventReceipt(BaseModel):
    event_id: UUID
    experiment_id: UUID
    event_type: Literal["VISIT", "SIGNUP", "ACTIVATED", "PAID"]
    attributed_by: str
    duplicate: bool = False


class SpendCreate(BaseModel):
    spend_id: UUID = Field(default_factory=uuid4)
    amount: float = Field(gt=0)
    occurred_at: datetime | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class SpendReceipt(BaseModel):
    spend_id: UUID
    experiment_id: UUID
    amount: float
    duplicate: bool = False


class ExperimentMetricsView(BaseModel):
    spend: float = Field(ge=0)
    visits: int = Field(ge=0)
    signups: int = Field(ge=0)
    activated_users: int = Field(ge=0)
    paid_users: int = Field(ge=0)
    transactions: int = Field(ge=0)
    revenue: float = Field(ge=0)
    visit_to_signup_rate: float | None = Field(default=None, ge=0)
    signup_to_paid_rate: float | None = Field(default=None, ge=0)
    cac: float | None = Field(default=None, ge=0)
    roas: float | None = Field(default=None, ge=0)
    revenue_per_paid_user: float | None = Field(default=None, ge=0)


class ExperimentAnalyticsView(BaseModel):
    experiment: ExperimentView
    event_count: int = Field(ge=0)
    metrics: ExperimentMetricsView


class ProductAnalyticsView(BaseModel):
    product_id: UUID
    experiment_count: int = Field(ge=0)
    total_spend: float = Field(ge=0)
    total_paid_users: int = Field(ge=0)
    total_revenue: float = Field(ge=0)
    blended_cac: float | None = Field(default=None, ge=0)
    blended_roas: float | None = Field(default=None, ge=0)
    experiments: list[ExperimentAnalyticsView]
