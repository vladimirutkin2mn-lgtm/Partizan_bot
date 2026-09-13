from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from app.channel_execution import PublisherMode
from app.distribution_types import DistributionPlatform


CustomerExecutionRequestStatus = Literal["REQUESTED"]


class CustomerExecutionRequestCreate(BaseModel):
    confirm_request: Literal[True]


class CustomerExecutionRequestView(BaseModel):
    id: UUID
    project_id: UUID
    product_id: UUID
    platform: DistributionPlatform
    publisher_mode: PublisherMode
    status: CustomerExecutionRequestStatus = "REQUESTED"
    source_title: str = Field(min_length=1, max_length=500)
    source_url: HttpUrl
    draft_title: str | None = Field(default=None, max_length=300)
    content_text: str = Field(min_length=10, max_length=12000)
    execution_allowed: Literal[False] = False
    customer_publish_confirmation_required: Literal[True] = True
    requested_at: datetime
