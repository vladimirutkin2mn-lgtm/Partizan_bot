from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, model_validator

from app.distribution_types import (
    DistributionActionType,
    DistributionPlatform,
    OpportunityKind,
)


class ManagedPublisherOwnership(StrEnum):
    PARTIZAN_MANAGED = "PARTIZAN_MANAGED"
    PARTNER_MANAGED = "PARTNER_MANAGED"


class ManagedPublisherHealth(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    PAUSED = "PAUSED"
    RESTRICTED = "RESTRICTED"
    RETIRED = "RETIRED"


class ManagedAssignmentStatus(StrEnum):
    RESERVED = "RESERVED"
    FULFILLED = "FULFILLED"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"


class ManagedPublisherRegistrationRequest(BaseModel):
    distribution_identity_id: UUID
    ownership: ManagedPublisherOwnership
    internal_label: str = Field(min_length=2, max_length=160)
    topic_verticals: list[str] = Field(min_length=1, max_length=20)
    languages: list[str] = Field(min_length=1, max_length=12)
    allowed_surfaces: list[OpportunityKind] = Field(min_length=1)
    allowed_actions: list[DistributionActionType] = Field(min_length=1)
    daily_action_capacity: int = Field(default=3, ge=1, le=20)
    prior_outcome_score: float = Field(default=50, ge=0, le=100)
    last_activity_at: datetime | None = None
    management_authorization_confirmed: bool = False
    partner_reference: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def validate_authorization(self) -> "ManagedPublisherRegistrationRequest":
        if not self.management_authorization_confirmed:
            raise ValueError("Managed publisher authorization must be explicitly confirmed")
        if self.ownership == ManagedPublisherOwnership.PARTNER_MANAGED:
            if not str(self.partner_reference or "").strip():
                raise ValueError("Partner-managed inventory requires a partner_reference")
        return self


class ManagedPublisherHealthRequest(BaseModel):
    health: ManagedPublisherHealth
    reason: str | None = Field(default=None, max_length=500)


class ManagedPublisherView(BaseModel):
    id: UUID
    distribution_identity_id: UUID
    ownership: ManagedPublisherOwnership
    internal_label: str
    platform: DistributionPlatform
    topic_verticals: list[str]
    languages: list[str]
    allowed_surfaces: list[OpportunityKind]
    allowed_actions: list[DistributionActionType]
    daily_action_capacity: int = Field(ge=1, le=20)
    prior_outcome_score: float = Field(ge=0, le=100)
    last_activity_at: datetime | None = None
    health: ManagedPublisherHealth
    health_reason: str | None = None
    partner_reference: str | None = None
    created_at: datetime
    updated_at: datetime


class ManagedSelectionRequest(BaseModel):
    platform: DistributionPlatform
    action_type: DistributionActionType
    opportunity_kind: OpportunityKind
    vertical: str = Field(min_length=1, max_length=160)
    language: str = Field(min_length=1, max_length=50)
    conflict_group: str | None = Field(default=None, max_length=160)


class ManagedSelectionCandidateView(BaseModel):
    managed_publisher_id: UUID
    distribution_identity_id: UUID
    ownership: ManagedPublisherOwnership
    score: float = Field(ge=0, le=100)
    capacity_remaining_24h: int = Field(ge=0)
    reasons: list[str] = Field(default_factory=list)


class ManagedAssignmentCreateRequest(ManagedSelectionRequest):
    opportunity_id: UUID | None = None


class ManagedFulfillmentRequest(BaseModel):
    action_id: UUID
    external_reference: str = Field(min_length=1, max_length=300)
    executed_url: HttpUrl | None = None
    distribution_spend_usd: float = Field(default=0, ge=0)
    operational_cost_usd: float = Field(default=0, ge=0)
    management_fee_usd: float = Field(default=0, ge=0)
    notes: str | None = Field(default=None, max_length=1000)


class ManagedCostBreakdown(BaseModel):
    distribution_spend_usd: float = Field(default=0, ge=0)
    operational_cost_usd: float = Field(default=0, ge=0)
    management_fee_usd: float = Field(default=0, ge=0)

    @property
    def total_usd(self) -> float:
        return self.distribution_spend_usd + self.operational_cost_usd + self.management_fee_usd


class ManagedAssignmentView(BaseModel):
    id: UUID
    product_id: UUID
    managed_publisher_id: UUID
    distribution_identity_id: UUID
    ownership: ManagedPublisherOwnership
    platform: DistributionPlatform
    action_type: DistributionActionType
    opportunity_kind: OpportunityKind
    opportunity_id: UUID | None = None
    campaign_slot_id: UUID
    conflict_group: str | None = None
    status: ManagedAssignmentStatus
    cost: ManagedCostBreakdown = Field(default_factory=ManagedCostBreakdown)
    action_id: UUID | None = None
    external_reference: str | None = None
    executed_url: HttpUrl | None = None
    reserved_at: datetime
    fulfilled_at: datetime | None = None
    released_at: datetime | None = None


class ManagedServiceStatusView(BaseModel):
    platform: DistributionPlatform
    service_label: str = "Partizan Managed Distribution"
    available: bool
    blocker: str | None = None


class CustomerManagedAssignmentView(BaseModel):
    id: UUID
    platform: DistributionPlatform
    service_label: str = "Partizan Managed Distribution"
    ownership: ManagedPublisherOwnership
    status: ManagedAssignmentStatus
    cost: ManagedCostBreakdown
    executed_url: HttpUrl | None = None
    fulfilled_at: datetime | None = None
