from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.channel_execution import PublisherMode
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_types import DistributionActionStatus, DistributionPlatform
from app.execution_adapters import ExecutionAdapterReceipt

CustomerExecutionRequestStatus = Literal[
    "REQUESTED",
    "PREPARATION_READY",
    "ACTION_PREPARED",
    "PUBLISH_CONFIRMED",
    "OPERATOR_APPROVED",
]


class CustomerExecutionRequestCreate(BaseModel):
    confirm_request: Literal[True]


class CustomerExecutionPreparationLinkRequest(BaseModel):
    distribution_play_id: UUID
    confirm_link: Literal[True]


class CustomerExecutionActionPrepareRequest(BaseModel):
    confirm_prepare: Literal[True]


class CustomerExecutionPublishConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_publish: Literal[True]
    creative_asset_id: UUID | None = None


class CustomerExecutionOperatorApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_approval: Literal[True]


class CustomerExecutionOperatorExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_execution: Literal[True]


class CustomerOperatorExecutionView(BaseModel):
    request_id: UUID
    distribution_action_id: UUID
    action_status: DistributionActionStatus
    experiment_status: DistributionExperimentStatus
    receipt: ExecutionAdapterReceipt | None = None
    retry_allowed: Literal[False] = False


class CustomerPreparedActionView(BaseModel):
    request_id: UUID
    project_id: UUID
    distribution_action_id: UUID
    platform: DistributionPlatform
    action_status: Literal["PREPARED", "APPROVED", "EXECUTED"] = "PREPARED"
    source_title: str = Field(min_length=1, max_length=500)
    source_url: HttpUrl
    target_url: HttpUrl
    draft_title: str | None = Field(default=None, max_length=300)
    context_text: str = Field(min_length=1, max_length=8000)
    content_text: str = Field(min_length=10, max_length=12000)
    creative_asset_id: UUID | None = None
    creative_asset_url: HttpUrl | None = None
    creative_brief_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    creative_blob_id: UUID | None = None
    creative_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    customer_publish_confirmed: bool = False
    customer_publish_confirmed_at: datetime | None = None
    operator_approved_at: datetime | None = None
    execution_allowed: Literal[False] = False
    operator_approval_required: bool = True
    published: bool = False


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
    context_text: str | None = Field(default=None, max_length=8000)
    content_text: str = Field(min_length=10, max_length=12000)
    distribution_play_id: UUID | None = None
    opportunity_id: UUID | None = None
    preparation_ready_at: datetime | None = None
    distribution_action_id: UUID | None = None
    experiment_id: UUID | None = None
    action_prepared_at: datetime | None = None
    customer_publish_confirmed_at: datetime | None = None
    customer_publish_confirmation_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    confirmed_creative_asset_id: UUID | None = None
    confirmed_creative_asset_url: HttpUrl | None = None
    confirmed_creative_brief_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    confirmed_creative_blob_id: UUID | None = None
    confirmed_creative_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    operator_approved_at: datetime | None = None
    execution_allowed: Literal[False] = False
    customer_publish_confirmation_required: Literal[True] = True
    requested_at: datetime
