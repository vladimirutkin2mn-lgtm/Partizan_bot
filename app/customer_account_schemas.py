from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.customer_schemas import (
    CustomerAutopilotOverview,
    CustomerDirectionView,
    CustomerProjectView,
)
from app.growth_autoresearch_schemas import GrowthAutoResearchOverviewView


class CustomerAccountRegisterRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    project_id: UUID
    customer_token: str = Field(min_length=20, max_length=255)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("Enter a valid email address")
        local, domain = normalized.rsplit("@", 1)
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("Enter a valid email address")
        return normalized


class CustomerAccountLoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().casefold()


class CustomerAccountClaimProjectRequest(BaseModel):
    project_id: UUID
    customer_token: str = Field(min_length=20, max_length=255)


class CustomerAccountProjectView(BaseModel):
    project_id: UUID
    brief: str
    market: str
    goal: str
    research_state: str
    launch_unlocked: bool
    created_at: datetime


class CustomerAccountView(BaseModel):
    account_id: UUID
    email: str
    projects: list[CustomerAccountProjectView] = Field(default_factory=list)


class CustomerWorkspaceView(BaseModel):
    account: CustomerAccountView
    project: CustomerProjectView
    autopilot: CustomerAutopilotOverview
    preview_directions: list[CustomerDirectionView] = Field(default_factory=list)
    autoresearch: GrowthAutoResearchOverviewView | None = None
    target_max_cac: float | None = None
    autonomous_spend_confirmed: bool = False
