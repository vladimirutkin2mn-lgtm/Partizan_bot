from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from app.models import ProductProfileStatus


class ProductCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=20)
    value_proposition: str | None = None
    use_cases: list[str] = Field(default_factory=list)
    market: str | None = None
    language: str | None = None
    price: float | None = Field(default=None, ge=0)
    pricing_model: str | None = None
    goal: str | None = None
    budget: float | None = Field(default=None, ge=0)
    max_cac: float | None = Field(default=None, ge=0)
    allowed_channels: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    reference_links: list[HttpUrl] = Field(default_factory=list)


class ClarificationQuestionView(BaseModel):
    id: UUID
    field_name: str
    question: str
    rationale: str


class ProductProfileView(BaseModel):
    id: UUID
    name: str
    description: str
    value_proposition: str | None
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
    reference_links: list[str]
    assumptions: list[str]
    status: ProductProfileStatus


class ProductIntakeResponse(BaseModel):
    product: ProductProfileView
    clarifications: list[ClarificationQuestionView]


class ClarificationAnswerRequest(BaseModel):
    question_id: UUID
    answer: str = Field(min_length=1)


class WorkflowStageView(BaseModel):
    name: str
    status: str


class MockWorkflowResponse(BaseModel):
    stages: list[WorkflowStageView]
