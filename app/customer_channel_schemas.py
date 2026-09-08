from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.channel_execution import ChannelCapability, PublisherMode
from app.distribution_types import DistributionPlatform

CustomerChannelMode = Literal["AUTO", "RESEARCH_ONLY", "OFF"]


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
