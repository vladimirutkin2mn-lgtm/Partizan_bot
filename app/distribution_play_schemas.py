from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.distribution_types import (
    AttributionLevel,
    AutomationLevel,
    DistributionActionType,
    DistributionPlatform,
    OpportunityKind,
)


class DistributionTacticClass(StrEnum):
    COMMUNITY = "COMMUNITY"
    PAID_PLATFORM = "PAID_PLATFORM"
    OWNED_ORGANIC = "OWNED_ORGANIC"
    OUTREACH = "OUTREACH"


class DistributionPlayStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class DistributionPlayView(BaseModel):
    id: UUID
    product_id: UUID
    icp_id: UUID
    opportunity_id: UUID
    platform: DistributionPlatform
    opportunity_kind: OpportunityKind
    opportunity_title: str
    tactic_id: str
    tactic_class: DistributionTacticClass
    action_type: DistributionActionType
    automation_level: AutomationLevel
    attribution_level: AttributionLevel
    identity_required: bool
    selected_identity_id: UUID | None = None
    community_policy_required: bool = False
    status: DistributionPlayStatus
    blockers: list[str] = Field(default_factory=list)
    hypothesis: str = Field(min_length=20)
    execution_steps: list[str] = Field(min_length=2)
    success_metric: str = Field(min_length=3)
    estimated_cost_min: float = Field(ge=0)
    estimated_cost_max: float = Field(ge=0)
    effort_hours: float = Field(gt=0)
    time_to_signal_days: int = Field(ge=1, le=90)
    priority_score: float = Field(ge=0, le=100)
    rationale: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_readiness(self) -> "DistributionPlayView":
        if self.estimated_cost_max < self.estimated_cost_min:
            raise ValueError("estimated_cost_max must be >= estimated_cost_min")
        if self.status == DistributionPlayStatus.READY and self.blockers:
            raise ValueError("READY distribution play cannot contain blockers")
        if self.status == DistributionPlayStatus.BLOCKED and not self.blockers:
            raise ValueError("BLOCKED distribution play must contain blockers")
        if self.identity_required and self.status == DistributionPlayStatus.READY:
            if self.selected_identity_id is None:
                raise ValueError("READY identity-backed play requires selected_identity_id")
        return self


class DistributionPlayGenerationResponse(BaseModel):
    product_id: UUID
    play_count: int = Field(ge=1)
    ready_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    plays: list[DistributionPlayView] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_counts(self) -> "DistributionPlayGenerationResponse":
        if self.play_count != len(self.plays):
            raise ValueError("play_count must match plays length")
        ready = sum(play.status == DistributionPlayStatus.READY for play in self.plays)
        blocked = sum(play.status == DistributionPlayStatus.BLOCKED for play in self.plays)
        if self.ready_count != ready or self.blocked_count != blocked:
            raise ValueError("ready_count/blocked_count do not match plays")
        return self
