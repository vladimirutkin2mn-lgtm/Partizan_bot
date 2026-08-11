from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from app.distribution_schemas import DistributionActionView
from app.distribution_types import AttributionLevel


class DistributionExperimentStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    CANCELLED = "CANCELLED"


class DistributionExecutionPrepareRequest(BaseModel):
    destination_url: HttpUrl | None = None
    target_url: HttpUrl | None = None
    context_text: str | None = Field(default=None, max_length=8000)
    content_text: str | None = Field(default=None, max_length=12000)


class DistributionActionEditRequest(BaseModel):
    target_url: HttpUrl | None = None
    context_text: str | None = Field(default=None, max_length=8000)
    content_text: str | None = Field(default=None, max_length=12000)


class DistributionActionExecutionRequest(BaseModel):
    external_reference: str | None = Field(default=None, max_length=500)
    executed_url: HttpUrl | None = None
    notes: str | None = Field(default=None, max_length=4000)


class DistributionExperimentView(BaseModel):
    id: UUID
    product_id: UUID
    distribution_play_id: UUID
    opportunity_id: UUID
    action_id: UUID
    status: DistributionExperimentStatus
    attribution_level: AttributionLevel
    tracking_url: str
    referral_token: str


class DistributionExecutionPlanView(BaseModel):
    action: DistributionActionView
    experiment: DistributionExperimentView
