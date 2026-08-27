from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class GrowthResearchOutcome(StrEnum):
    KEEP = "KEEP"
    DISCARD = "DISCARD"
    INCONCLUSIVE = "INCONCLUSIVE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class GrowthResearchTrialStatus(StrEnum):
    READY = "READY"
    EVALUATED = "EVALUATED"


class GrowthResearchPolicyRequest(BaseModel):
    allowed_platforms: list[str] = Field(default_factory=list, max_length=32)
    max_changed_dimensions: int = Field(default=2, ge=1, le=2)
    max_shadow_trial_budget: float = Field(default=0, ge=0)
    min_paid_users_for_decision: int = Field(default=3, ge=1, le=100000)
    min_relative_cac_improvement: float = Field(default=0.05, ge=0, le=1)
    paused: bool = False


class GrowthResearchPolicyView(GrowthResearchPolicyRequest):
    product_id: UUID
    shadow_only: bool = True
    created_at: datetime
    updated_at: datetime


class GrowthVariantSpec(BaseModel):
    platform: str = Field(min_length=1, max_length=64)
    tactic_id: str = Field(min_length=1, max_length=160)
    audience: str | None = Field(default=None, max_length=1000)
    message_angle: str | None = Field(default=None, max_length=2000)
    offer: str | None = Field(default=None, max_length=2000)
    creative_ref: str | None = Field(default=None, max_length=1000)
    cta: str | None = Field(default=None, max_length=1000)
    destination_url: str | None = Field(default=None, max_length=2000)
    targeting: str | None = Field(default=None, max_length=2000)
    timing: str | None = Field(default=None, max_length=1000)
    test_budget: float = Field(default=0, ge=0)


class GrowthResearchEvidence(BaseModel):
    spend: float = Field(default=0, ge=0)
    visits: int = Field(default=0, ge=0)
    signups: int = Field(default=0, ge=0)
    activated_users: int = Field(default=0, ge=0)
    paid_users: int = Field(default=0, ge=0)
    revenue: float = Field(default=0, ge=0)
    source: str = Field(default="shadow", min_length=1, max_length=120)


class GrowthResearchBaselineRequest(BaseModel):
    variant: GrowthVariantSpec
    evidence: GrowthResearchEvidence


class GrowthResearchChallengerRequest(BaseModel):
    variant: GrowthVariantSpec


class GrowthResearchEvaluationRequest(BaseModel):
    evidence: GrowthResearchEvidence
    blocked_reason: str | None = Field(default=None, max_length=2000)
    failed_reason: str | None = Field(default=None, max_length=2000)


class GrowthChampionView(BaseModel):
    id: UUID
    product_id: UUID
    variant: GrowthVariantSpec
    evidence: GrowthResearchEvidence
    source_trial_id: UUID | None = None
    promoted_at: datetime


class GrowthResearchTrialView(BaseModel):
    id: UUID
    product_id: UUID
    champion_id: UUID
    challenger: GrowthVariantSpec
    changed_dimensions: list[str]
    status: GrowthResearchTrialStatus
    evaluation_id: UUID | None = None
    created_at: datetime
    evaluated_at: datetime | None = None


class GrowthResearchEvaluationView(BaseModel):
    id: UUID
    product_id: UUID
    trial_id: UUID
    champion_id: UUID
    outcome: GrowthResearchOutcome
    rationale: list[str]
    champion_evidence: GrowthResearchEvidence
    challenger_evidence: GrowthResearchEvidence
    champion_cac: float | None = None
    challenger_cac: float | None = None
    created_at: datetime


class GrowthResearchHistoryView(BaseModel):
    product_id: UUID
    policy: GrowthResearchPolicyView | None = None
    champion: GrowthChampionView | None = None
    trials: list[GrowthResearchTrialView] = Field(default_factory=list)
    evaluations: list[GrowthResearchEvaluationView] = Field(default_factory=list)
