from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class GrowthAutoResearchExecutionStatus(StrEnum):
    PREPARED = "PREPARED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTED = "EXECUTED"
    ASSISTED = "ASSISTED"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    ERROR = "ERROR"


class GrowthAutoResearchExecutionView(BaseModel):
    id: UUID
    product_id: UUID
    trial_id: UUID
    play_id: UUID | None = None
    action_id: UUID | None = None
    experiment_id: UUID | None = None
    mandate_id: UUID | None = None
    mandate_version: int | None = Field(default=None, ge=1)
    platform: str
    action_type: str | None = None
    status: GrowthAutoResearchExecutionStatus
    adapter_outcome: str | None = None
    proposed_spend: float = Field(default=0, ge=0, le=0)
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class GrowthAutoResearchExecutionSweepView(BaseModel):
    product_id: UUID | None = None
    attempted_count: int = Field(ge=0)
    executed_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    executions: list[GrowthAutoResearchExecutionView] = Field(default_factory=list)
    created_at: datetime
