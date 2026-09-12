from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class CustomerDistributionLearningEntryView(BaseModel):
    experiment_id: UUID
    platform: str
    opportunity_title: str
    opportunity_url: HttpUrl | None = None
    publisher_mode: str
    action_type: str | None = None
    decision: Literal["SCALE", "CONTINUE", "MODIFY", "STOP"]
    observed_cac: float | None = Field(default=None, ge=0)
    paid_users: int = Field(default=0, ge=0)
    revenue: float = Field(default=0, ge=0)
    replies: int = Field(default=0, ge=0)
    removals: int = Field(default=0, ge=0)
    observed_basis: list[str] = Field(default_factory=list)
    created_at: datetime


class CustomerDistributionLearningView(BaseModel):
    project_id: UUID
    product_id: UUID | None = None
    entries: list[CustomerDistributionLearningEntryView] = Field(default_factory=list)
