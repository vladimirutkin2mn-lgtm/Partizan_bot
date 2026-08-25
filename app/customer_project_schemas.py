from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.customer_account_schemas import CustomerAccountView

CustomerProjectType = Literal[
    "WEBSITE_PRODUCT",
    "TELEGRAM_COMMUNITY",
    "SOCIAL_ACCOUNT",
    "APP",
    "BUSINESS_SERVICE",
    "OTHER",
]


class CustomerAccountCreateProjectRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    project_type: CustomerProjectType
    reference_url: HttpUrl | None = None
    brief: str = Field(min_length=20, max_length=6000)
    market: str = Field(min_length=2, max_length=160)
    goal: str = Field(min_length=2, max_length=200)
    budget_usd: int = Field(ge=1, le=100_000)

    @field_validator("name", "market", "goal", "brief")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class CustomerAccountCreateProjectResponse(BaseModel):
    project_id: UUID
    account: CustomerAccountView


class CustomerAccountProjectNavView(BaseModel):
    project_id: UUID
    name: str
    project_type: CustomerProjectType | None = None
    reference_url: HttpUrl | None = None
    market: str
    goal: str
    created_at: datetime
