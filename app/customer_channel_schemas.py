from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, model_validator

from app.channel_execution import ChannelCapability, PublisherMode
from app.customer_schemas import CustomerResearchEvidenceView
from app.distribution_types import DistributionPlatform

CustomerChannelMode = Literal["AUTO", "RESEARCH_ONLY", "OFF"]
CustomerStartingMoveDraftReviewStatus = Literal["DRAFT", "ACCEPTED", "REJECTED"]


class CustomerChannelPreferenceInput(BaseModel):
    platform: DistributionPlatform
    mode: CustomerChannelMode | None = None
    publisher_mode: PublisherMode | None = None

    @model_validator(mode="after")
    def validate_change_present(self) -> CustomerChannelPreferenceInput:
        if self.mode is None and self.publisher_mode is None:
            raise ValueError("Set channel mode, publisher mode, or both")
        return self


class CustomerChannelPreferencesUpdateRequest(BaseModel):
    channels: list[CustomerChannelPreferenceInput] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def validate_unique_platforms(self) -> CustomerChannelPreferencesUpdateRequest:
        platforms = [item.platform for item in self.channels]
        if len(platforms) != len(set(platforms)):
            raise ValueError("Each channel can be configured only once")
        return self


class CustomerChannelSelectionRequest(BaseModel):
    platform: DistributionPlatform


class CustomerStartingMoveView(BaseModel):
    platform: DistributionPlatform
    channel_label: str
    state: Literal["READY", "NEEDS_RESEARCH"]
    source: Literal[
        "FULL_RESEARCH",
        "CHANNEL_RESEARCH",
        "PREVIEW_RESEARCH",
        "SELECTED_CHANNEL",
    ]
    title: str
    rationale: str
    recommended_action: str
    signal_to_watch: str
    execution_requirement: str
    url: HttpUrl | None = None
    provenance: list[CustomerResearchEvidenceView] = Field(default_factory=list)


class CustomerStartingMoveDraftEditRequest(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    content_text: str = Field(min_length=10, max_length=12000)


class CustomerStartingMoveDraftView(BaseModel):
    project_id: UUID
    platform: DistributionPlatform
    channel_label: str
    state: Literal["REVIEW_ONLY"] = "REVIEW_ONLY"
    review_status: CustomerStartingMoveDraftReviewStatus = "DRAFT"
    source_title: str
    source_url: HttpUrl
    title: str | None = None
    context_text: str = Field(min_length=10, max_length=8000)
    content_text: str = Field(min_length=10, max_length=12000)
    rationale: str = Field(min_length=5, max_length=2000)
    signal_to_watch: str
    execution_allowed: Literal[False] = False
    execution_requirement: str
    provenance: list[CustomerResearchEvidenceView] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime | None = None


class CustomerChannelCapabilityView(BaseModel):
    capability: ChannelCapability
    ready: bool
    blocker: str | None = None


class CustomerPublisherModeView(BaseModel):
    mode: PublisherMode
    available: bool
    blocker: str | None = None


class CustomerChannelView(BaseModel):
    platform: DistributionPlatform
    label: str
    mode: CustomerChannelMode
    selected: bool = False
    publisher_mode: PublisherMode = PublisherMode.MANUAL
    publisher_modes: list[CustomerPublisherModeView] = Field(default_factory=list)
    capabilities: list[CustomerChannelCapabilityView] = Field(default_factory=list)
    autonomous_execution_available: bool
    execution_ready: bool = False
    execution_blocker: str | None = None
    connected: bool | None = None
    experiment_count: int = Field(default=0, ge=0)
    spend_usd: float = Field(default=0, ge=0)
    paid_customers: int = Field(default=0, ge=0)
    revenue_usd: float = Field(default=0, ge=0)
    cac_usd: float | None = Field(default=None, ge=0)
    roas: float | None = Field(default=None, ge=0)
