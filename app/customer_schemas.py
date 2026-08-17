from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class CustomerPreviewRequest(BaseModel):
    brief: str = Field(min_length=20, max_length=6000)
    market: str = Field(min_length=2, max_length=160)
    goal: str = Field(min_length=2, max_length=200)
    budget_usd: int = Field(ge=100, le=100_000)


class CustomerDirectionView(BaseModel):
    name: str
    potential: Literal["HIGH", "MEDIUM"]
    rationale: str


class MaskedOpportunityView(BaseModel):
    category: str
    label: str


class CustomerPreviewResponse(BaseModel):
    project_id: UUID
    customer_token: str
    channel_count: int = Field(ge=3, le=5)
    opportunity_scope_estimate: int = Field(ge=1)
    fastest_signal: str
    directions: list[CustomerDirectionView]
    masked_opportunities: list[MaskedOpportunityView]
    launch_price_usd: int = Field(ge=1)
    autopilot_price_usd: int = Field(ge=1)
    managed_spend_fee_pct: int = Field(ge=0, le=100)


class CustomerProjectView(BaseModel):
    project_id: UUID
    status: Literal["PREVIEW", "CHECKOUT_PENDING", "UNLOCKED", "RESEARCH_READY"]
    brief: str
    market: str
    goal: str
    budget_usd: int
    launch_unlocked: bool
    research_state: Literal["NOT_STARTED", "NEEDS_INPUT", "READY"]
    product_id: UUID | None = None
    launch_price_usd: int
    autopilot_price_usd: int
    managed_spend_fee_pct: int


class CheckoutResponse(BaseModel):
    checkout_url: HttpUrl | None = None
    already_unlocked: bool = False


class CustomerClarificationView(BaseModel):
    question_id: UUID
    question: str
    rationale: str


class CustomerICPView(BaseModel):
    title: str
    description: str
    score: float
    message_hook: str


class CustomerOpportunityView(BaseModel):
    platform: str
    kind: str
    title: str
    url: HttpUrl | None = None
    rationale: str | None = None
    relevance_score: float | None = None


class CustomerResearchResponse(BaseModel):
    project_id: UUID
    state: Literal["NEEDS_INPUT", "READY"]
    message: str
    product_id: UUID
    clarifications: list[CustomerClarificationView] = Field(default_factory=list)
    icps: list[CustomerICPView] = Field(default_factory=list)
    opportunities: list[CustomerOpportunityView] = Field(default_factory=list)


class CustomerClarificationAnswerRequest(BaseModel):
    question_id: UUID
    answer: str = Field(min_length=1, max_length=2000)
