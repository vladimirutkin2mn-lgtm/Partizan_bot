from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from app.models import ProductProfileStatus


class ProductCreateRequest(BaseModel):
    brief: str = Field(min_length=20)
    reference_links: list[HttpUrl] = Field(default_factory=list)


class ClarificationQuestionView(BaseModel):
    id: UUID
    field_name: str
    question: str
    rationale: str
    priority: int = Field(ge=1, le=5)


class ProductProfileView(BaseModel):
    id: UUID
    input_brief: str
    name: str
    description: str
    problem_or_desire: str | None
    value_proposition: str | None
    usp: str | None
    use_cases: list[str]
    market: str | None
    language: str | None
    price: float | None
    pricing_model: str | None
    goal: str | None
    budget: float | None
    max_cac: float | None
    allowed_channels: list[str]
    constraints: list[str]
    known_audience: list[str]
    known_competitors: list[str]
    reference_links: list[str]
    assumptions: list[str]
    contradictions: list[str]
    status: ProductProfileStatus


class ProductIntakeResponse(BaseModel):
    product: ProductProfileView
    clarifications: list[ClarificationQuestionView]
    next_action: Literal["answer_clarifications", "confirm", "start_growth"]


class ClarificationAnswerRequest(BaseModel):
    question_id: UUID
    answer: str = Field(min_length=1)


class ICPScoreBreakdownView(BaseModel):
    pain_intensity: int = Field(ge=1, le=10)
    purchase_intent: int = Field(ge=1, le=10)
    willingness_to_pay: int = Field(ge=1, le=10)
    ease_of_targeting: int = Field(ge=1, le=10)
    market_size: int = Field(ge=1, le=10)
    competitive_headroom: int = Field(ge=1, le=10)
    speed_of_validation: int = Field(ge=1, le=10)


class ICPView(BaseModel):
    id: UUID
    product_id: UUID
    rank: int = Field(ge=1)
    title: str
    description: str
    pain: str
    desired_outcome: str
    trigger: str
    willingness_to_pay: str
    alternatives: list[str]
    message_hook: str
    score: float = Field(ge=0, le=100)
    score_breakdown: ICPScoreBreakdownView
    score_explanation: str
    rationale: list[str]
    duplicate_of: str | None = None


class DuplicateClusterView(BaseModel):
    canonical: str
    duplicates: list[str]


class ICPGenerationResponse(BaseModel):
    product_id: UUID
    generated_count: int = Field(ge=10)
    ranked_count: int = Field(ge=10)
    icps: list[ICPView] = Field(min_length=10)
    duplicate_clusters: list[DuplicateClusterView] = Field(default_factory=list)


class ChannelEvidenceView(BaseModel):
    query: str
    title: str
    url: str
    snippet: str


class ChannelOpportunityView(BaseModel):
    id: UUID
    icp_id: UUID
    source_type: Literal["community", "creator", "newsletter_site"]
    platform: str
    title: str
    url: str
    relevance_score: float = Field(ge=0, le=100)
    rationale: str
    evidence: list[ChannelEvidenceView] = Field(min_length=1)


class ChannelDiscoveryResponse(BaseModel):
    product_id: UUID
    top_icp_count: int = Field(ge=1)
    opportunity_count: int = Field(ge=30)
    opportunities: list[ChannelOpportunityView] = Field(min_length=30)


class PlayScoreBreakdownView(BaseModel):
    expected_impact: int = Field(ge=1, le=10)
    confidence: int = Field(ge=1, le=10)
    cost_efficiency: int = Field(ge=1, le=10)
    speed_to_signal: int = Field(ge=1, le=10)


class GrowthPlayView(BaseModel):
    id: UUID
    product_id: UUID
    rank: int = Field(ge=1)
    icp_id: UUID
    channel_id: UUID
    source_type: Literal["community", "creator", "newsletter_site"]
    channel_url: str
    template_id: str
    hypothesis: str
    offer: str
    execution_steps: list[str] = Field(min_length=3)
    success_metric: str
    expected_result: str
    kill_criteria: str
    scale_criteria: str
    estimated_cost_min: float = Field(ge=0)
    estimated_cost_max: float = Field(ge=0)
    effort_hours: float = Field(gt=0)
    time_to_signal_days: int = Field(ge=1, le=90)
    priority_score: float = Field(ge=0, le=100)
    score_breakdown: PlayScoreBreakdownView
    score_explanation: str
    rationale: list[str]
    status: Literal["PROPOSED", "APPROVED", "REJECTED"]


class GrowthPlayGenerationResponse(BaseModel):
    product_id: UUID
    play_count: int = Field(ge=20)
    plays: list[GrowthPlayView] = Field(min_length=20)


class GrowthPlayApprovalRequest(BaseModel):
    status: Literal["APPROVED", "REJECTED"]


class WorkflowStageView(BaseModel):
    name: str
    status: str


class MockWorkflowResponse(BaseModel):
    stages: list[WorkflowStageView]
