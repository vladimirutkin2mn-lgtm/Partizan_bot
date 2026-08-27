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


class GrowthResearchObjective(StrEnum):
    PAID_CAC = "PAID_CAC"
    PAID_CONVERSION = "PAID_CONVERSION"
    ACTIVATION_CONVERSION = "ACTIVATION_CONVERSION"
    SIGNUP_CONVERSION = "SIGNUP_CONVERSION"
    NONE = "NONE"


class GrowthHypothesisMode(StrEnum):
    AUTO = "AUTO"
    EXPLOIT = "EXPLOIT"
    EXPLORE = "EXPLORE"


class GrowthResearchTrialStatus(StrEnum):
    READY = "READY"
    EVALUATED = "EVALUATED"


class GrowthAutoResearchLoopStatus(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    PAUSED = "PAUSED"
    NO_BASELINE = "NO_BASELINE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    WAITING_EVIDENCE = "WAITING_EVIDENCE"
    GENERATED = "GENERATED"
    IDLE = "IDLE"


class GrowthResearchPolicyRequest(BaseModel):
    allowed_platforms: list[str] = Field(default_factory=list, max_length=32)
    max_changed_dimensions: int = Field(default=2, ge=1, le=2)
    max_shadow_trial_budget: float = Field(default=0, ge=0)
    shadow_research_budget: float | None = Field(default=None, ge=0)
    max_trial_budget_share: float = Field(default=0.25, gt=0, le=1)
    max_trial_duration_hours: float = Field(default=168, gt=0, le=24 * 90)
    min_paid_users_for_decision: int = Field(default=3, ge=2, le=100000)
    min_activated_users_for_decision: int = Field(default=5, ge=2, le=100000)
    min_signups_for_decision: int = Field(default=10, ge=2, le=100000)
    min_visits_for_proxy_decision: int = Field(default=100, ge=10, le=10000000)
    min_relative_cac_improvement: float = Field(default=0.05, ge=0, le=1)
    min_relative_proxy_improvement: float = Field(default=0.10, ge=0, le=1)
    max_relative_roas_regression: float = Field(default=0.15, ge=0, le=1)
    confidence_level: float = Field(default=0.90, ge=0.5, lt=1)
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
    impressions: int = Field(default=0, ge=0)
    clicks: int = Field(default=0, ge=0)
    visits: int = Field(default=0, ge=0)
    signups: int = Field(default=0, ge=0)
    activated_users: int = Field(default=0, ge=0)
    paid_users: int = Field(default=0, ge=0)
    revenue: float = Field(default=0, ge=0)
    duration_hours: float = Field(default=0, ge=0)
    source: str = Field(default="shadow", min_length=1, max_length=120)


class GrowthResearchProvenanceView(BaseModel):
    source_domain: str = Field(default="DISTRIBUTION", min_length=1, max_length=64)
    surface: str = Field(default="EXECUTION_PLATFORM", min_length=1, max_length=64)
    platform: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=1, max_length=500)
    url: str | None = Field(default=None, max_length=2000)
    rationale: str | None = Field(default=None, max_length=4000)
    relevance_score: float | None = Field(default=None, ge=0, le=100)
    execution_status: str | None = Field(default=None, max_length=64)
    execution_requirement: str | None = Field(default=None, max_length=1000)
    source_urls: list[str] = Field(default_factory=list, max_length=20)
    signal_tags: list[str] = Field(default_factory=list, max_length=40)
    evidence_queries: list[str] = Field(default_factory=list, max_length=20)
    evidence_snippets: list[str] = Field(default_factory=list, max_length=20)


class GrowthResearchBaselineRequest(BaseModel):
    variant: GrowthVariantSpec
    evidence: GrowthResearchEvidence


class GrowthResearchChallengerRequest(BaseModel):
    variant: GrowthVariantSpec
    hypothesis: str | None = Field(default=None, max_length=4000)
    hypothesis_rationale: list[str] = Field(default_factory=list, max_length=20)
    hypothesis_mode: GrowthHypothesisMode | None = None
    hypothesis_source: str | None = Field(default=None, max_length=120)


class GrowthHypothesisGenerationRequest(BaseModel):
    mode: GrowthHypothesisMode = GrowthHypothesisMode.AUTO


class GrowthHypothesisDraft(BaseModel):
    mode: GrowthHypothesisMode
    hypothesis: str = Field(min_length=20, max_length=4000)
    rationale: list[str] = Field(min_length=1, max_length=20)
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
    hypothesis: str | None = None
    hypothesis_rationale: list[str] = Field(default_factory=list)
    hypothesis_mode: GrowthHypothesisMode | None = None
    hypothesis_source: str | None = None
    research_provenance: list[GrowthResearchProvenanceView] = Field(default_factory=list)
    evaluation_id: UUID | None = None
    created_at: datetime
    evaluated_at: datetime | None = None


class GrowthHypothesisGenerationView(BaseModel):
    product_id: UUID
    mode: GrowthHypothesisMode
    hypothesis: str
    rationale: list[str]
    changed_dimensions: list[str]
    source: str
    remaining_research_budget: float | None = None
    trial: GrowthResearchTrialView


class GrowthResearchEvaluationView(BaseModel):
    id: UUID
    product_id: UUID
    trial_id: UUID
    champion_id: UUID
    outcome: GrowthResearchOutcome
    objective: GrowthResearchObjective = GrowthResearchObjective.NONE
    rationale: list[str]
    champion_evidence: GrowthResearchEvidence
    challenger_evidence: GrowthResearchEvidence
    champion_cac: float | None = None
    challenger_cac: float | None = None
    champion_roas: float | None = None
    challenger_roas: float | None = None
    champion_metric_value: float | None = None
    challenger_metric_value: float | None = None
    relative_improvement: float | None = None
    confidence: float | None = None
    created_at: datetime


class GrowthResearchHistoryView(BaseModel):
    product_id: UUID
    policy: GrowthResearchPolicyView | None = None
    champion: GrowthChampionView | None = None
    trials: list[GrowthResearchTrialView] = Field(default_factory=list)
    evaluations: list[GrowthResearchEvaluationView] = Field(default_factory=list)


class GrowthAutoResearchSweepView(BaseModel):
    id: UUID
    product_id: UUID
    status: GrowthAutoResearchLoopStatus
    message: str
    trial_id: UUID | None = None
    provenance_count: int = Field(default=0, ge=0)
    remaining_research_budget: float | None = Field(default=None, ge=0)
    created_at: datetime


class GrowthAutoResearchRunView(BaseModel):
    id: UUID
    product_count: int = Field(ge=0)
    generated_count: int = Field(ge=0)
    waiting_count: int = Field(ge=0)
    sweeps: list[GrowthAutoResearchSweepView] = Field(default_factory=list)
    created_at: datetime


class GrowthAutoResearchOverviewView(BaseModel):
    product_id: UUID
    configured: bool
    paused: bool
    status: GrowthAutoResearchLoopStatus
    remaining_research_budget: float | None = Field(default=None, ge=0)
    champion: GrowthChampionView | None = None
    active_trial: GrowthResearchTrialView | None = None
    recent_trials: list[GrowthResearchTrialView] = Field(default_factory=list)
    recent_evaluations: list[GrowthResearchEvaluationView] = Field(default_factory=list)
    provenance: list[GrowthResearchProvenanceView] = Field(default_factory=list)
    last_sweep: GrowthAutoResearchSweepView | None = None
    research_only: bool = True
