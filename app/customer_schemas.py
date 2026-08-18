from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator


class CustomerPreviewRequest(BaseModel):
    brief: str = Field(min_length=20, max_length=6000)
    website_url: HttpUrl | None = None
    market: str = Field(min_length=2, max_length=160)
    goal: str = Field(min_length=2, max_length=200)
    budget_usd: int = Field(ge=1, le=100_000)


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
    website_url: HttpUrl | None = None
    market: str
    goal: str
    budget_usd: int
    launch_unlocked: bool
    research_state: Literal["NOT_STARTED", "NEEDS_INPUT", "READY"]
    product_id: UUID | None = None
    autopilot_subscription_status: Literal[
        "INACTIVE", "CHECKOUT_PENDING", "ACTIVE", "PAST_DUE", "CANCELLED"
    ] = "INACTIVE"
    autopilot_subscription_id: str | None = None
    launch_price_usd: int
    autopilot_price_usd: int
    managed_spend_fee_pct: int


class CheckoutResponse(BaseModel):
    checkout_url: HttpUrl | None = None
    already_unlocked: bool = False


class CustomerAccessRecoveryRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=255)


class CustomerAccessRecoveryResponse(BaseModel):
    project_id: UUID
    customer_token: str


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


class CustomerAutopilotVerifyRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=255)


class CustomerAutopilotConfigureRequest(BaseModel):
    marketing_budget_usd: float = Field(ge=100, le=100_000)
    target_max_cac: float = Field(gt=0, le=100_000)
    max_autonomous_spend_per_experiment: float | None = Field(default=None, gt=0)
    max_autonomous_spend_per_day: float | None = Field(default=None, gt=0)
    confirm_autonomous_spend: bool


class CustomerAutopilotStatusRequest(BaseModel):
    status: Literal["ACTIVE", "PAUSED"]


class CustomerMetaConnectResponse(BaseModel):
    authorization_url: HttpUrl


class CustomerMetaAdAccountOption(BaseModel):
    id: str
    account_id: str
    name: str
    currency: str | None = None


class CustomerMetaPageOption(BaseModel):
    id: str
    name: str


class CustomerMetaOptionsView(BaseModel):
    connected_to_meta: bool
    ad_accounts: list[CustomerMetaAdAccountOption] = Field(default_factory=list)
    pages_by_ad_account: dict[str, list[CustomerMetaPageOption]] = Field(default_factory=dict)


class CustomerMetaConnectionRequest(BaseModel):
    ad_account_id: str = Field(min_length=1, max_length=120)
    page_id: str = Field(min_length=1, max_length=120)
    instagram_actor_id: str | None = Field(default=None, max_length=120)
    country_codes: list[str] = Field(min_length=1, max_length=10)

    @field_validator("country_codes")
    @classmethod
    def normalize_country_codes(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            code = value.strip().upper()
            if len(code) != 2 or not code.isalpha():
                raise ValueError("country_codes must contain two-letter country codes")
            if code not in normalized:
                normalized.append(code)
        return normalized


class CustomerMetaConnectionView(BaseModel):
    connected: bool
    ad_account_id: str | None = None
    page_id: str | None = None
    instagram_actor_id: str | None = None
    country_codes: list[str] = Field(default_factory=list)


class CustomerAutopilotExperimentView(BaseModel):
    experiment_id: UUID
    platform: str
    action_type: str
    status: str
    budget_cap: float | None = None


class CustomerAutopilotDecisionView(BaseModel):
    recorded_at: datetime
    kind: str
    outcome: str
    decision: str | None = None
    reasons: list[str] = Field(default_factory=list)


class CustomerAutopilotOverview(BaseModel):
    project_id: UUID
    product_id: UUID
    subscription_status: str
    autopilot_status: str
    setup_complete: bool
    blockers: list[str] = Field(default_factory=list)
    marketing_budget_usd: float = Field(ge=0)
    spent_usd: float = Field(ge=0)
    remaining_budget_usd: float = Field(ge=0)
    paid_customers: int = Field(ge=0)
    revenue_usd: float = Field(ge=0)
    cac_usd: float | None = Field(default=None, ge=0)
    roas: float | None = Field(default=None, ge=0)
    managed_spend_fee_pct: int = Field(ge=0, le=100)
    estimated_managed_fee_usd: float = Field(ge=0)
    meta: CustomerMetaConnectionView
    running_experiments: list[CustomerAutopilotExperimentView] = Field(default_factory=list)
    waiting_experiments: list[CustomerAutopilotExperimentView] = Field(default_factory=list)
    recent_decisions: list[CustomerAutopilotDecisionView] = Field(default_factory=list)
