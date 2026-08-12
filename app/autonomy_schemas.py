from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.distribution_types import DistributionActionType, DistributionPlatform


class GrowthMandateStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    REVOKED = "REVOKED"


class AutonomyDecision(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    BLOCK = "BLOCK"


class GrowthMandateUpsertRequest(BaseModel):
    total_budget_cap: float = Field(gt=0)
    target_max_cac: float | None = Field(default=None, gt=0)
    max_autonomous_spend_per_experiment: float = Field(ge=0)
    max_autonomous_spend_per_day: float = Field(ge=0)
    max_concurrent_running_experiments: int | None = Field(default=None, ge=1)
    allowed_platforms: list[DistributionPlatform] = Field(min_length=1)
    allowed_actions: list[DistributionActionType] = Field(min_length=1)
    autonomous_prepare: bool = True
    autonomous_approve: bool = False
    autonomous_paid_activation: bool = False
    approval_threshold: float | None = Field(default=None, ge=0)
    effective_from: datetime | None = None
    effective_until: datetime | None = None

    @model_validator(mode="after")
    def validate_mandate(self) -> "GrowthMandateUpsertRequest":
        if self.max_autonomous_spend_per_experiment > self.total_budget_cap:
            raise ValueError(
                "max_autonomous_spend_per_experiment cannot exceed total_budget_cap"
            )
        if self.max_autonomous_spend_per_day > self.total_budget_cap:
            raise ValueError("max_autonomous_spend_per_day cannot exceed total_budget_cap")
        if self.approval_threshold is not None and self.approval_threshold > self.total_budget_cap:
            raise ValueError("approval_threshold cannot exceed total_budget_cap")
        if self.effective_from is not None and self.effective_until is not None:
            if self.effective_until <= self.effective_from:
                raise ValueError("effective_until must be after effective_from")
        if self.autonomous_paid_activation and not self.autonomous_approve:
            raise ValueError(
                "autonomous_paid_activation requires autonomous_approve to be enabled"
            )
        return self


class GrowthMandateStatusRequest(BaseModel):
    status: GrowthMandateStatus


class GrowthMandateView(BaseModel):
    id: UUID
    product_id: UUID
    version: int = Field(ge=1)
    status: GrowthMandateStatus
    total_budget_cap: float
    target_max_cac: float | None = None
    max_autonomous_spend_per_experiment: float
    max_autonomous_spend_per_day: float
    max_concurrent_running_experiments: int | None = None
    allowed_platforms: list[DistributionPlatform]
    allowed_actions: list[DistributionActionType]
    autonomous_prepare: bool
    autonomous_approve: bool
    autonomous_paid_activation: bool
    approval_threshold: float | None = None
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AutonomyEvaluationRequest(BaseModel):
    platform: DistributionPlatform
    action_type: DistributionActionType
    proposed_budget: float = Field(default=0, ge=0)
    requires_prepare: bool = True
    requires_approval: bool = True
    requests_paid_activation: bool = False


class AutonomyEvaluationView(BaseModel):
    decision: AutonomyDecision
    reasons: list[str]
    mandate_id: UUID | None = None
    mandate_version: int | None = None
    current_total_spend: float = 0
    current_daily_spend: float = 0
    remaining_total_budget: float = 0
    remaining_daily_budget: float = 0
    running_experiments: int = 0
