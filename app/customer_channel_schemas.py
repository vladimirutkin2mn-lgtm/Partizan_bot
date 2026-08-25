from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.distribution_types import DistributionPlatform

CustomerChannelMode = Literal["AUTO", "RESEARCH_ONLY", "OFF"]


class CustomerChannelPreferenceInput(BaseModel):
    platform: DistributionPlatform
    mode: CustomerChannelMode


class CustomerChannelPreferencesUpdateRequest(BaseModel):
    channels: list[CustomerChannelPreferenceInput] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def validate_unique_platforms(self) -> CustomerChannelPreferencesUpdateRequest:
        platforms = [item.platform for item in self.channels]
        if len(platforms) != len(set(platforms)):
            raise ValueError("Each channel can be configured only once")
        return self


class CustomerChannelView(BaseModel):
    platform: DistributionPlatform
    label: str
    mode: CustomerChannelMode
    autonomous_execution_available: bool
    connected: bool | None = None
    experiment_count: int = Field(default=0, ge=0)
    spend_usd: float = Field(default=0, ge=0)
    paid_customers: int = Field(default=0, ge=0)
    revenue_usd: float = Field(default=0, ge=0)
    cac_usd: float | None = Field(default=None, ge=0)
    roas: float | None = Field(default=None, ge=0)
