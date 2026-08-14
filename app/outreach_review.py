from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.distribution_execution_schemas import DistributionActionEditRequest
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import DistributionActionStatus
from app.outreach_briefs import (
    OUTREACH_BRIEF_NAMESPACE,
    OutreachBriefStatus,
    OutreachBriefView,
    outreach_brief_service,
)
from app.outreach_sender import outreach_sender_service
from app.outreach_targets import outreach_target_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

_URL_PATTERN = re.compile(r"(?i)(?:https?://|\bwww\.)")


class OutreachBriefEditRequest(BaseModel):
    message_subject: str = Field(min_length=3, max_length=180)
    message_body_without_link: str = Field(min_length=40, max_length=6000)

    @model_validator(mode="after")
    def validate_message(self) -> "OutreachBriefEditRequest":
        if "\r" in self.message_subject or "\n" in self.message_subject:
            raise ValueError("Outreach subject must be a single line")
        if _URL_PATTERN.search(self.message_body_without_link):
            raise ValueError(
                "Outreach body edits must not add URLs; Partizan preserves the exact tracking URL"
            )
        return self


class OutreachBriefReviewService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def edit(self, brief_id: UUID, payload: OutreachBriefEditRequest) -> OutreachBriefView:
        brief = self._reviewable_brief(brief_id, require_executable_target=True)
        subject = payload.message_subject.strip()
        body_without_link = payload.message_body_without_link.strip()
        message_body = f"{body_without_link}\n\nProduct details: {brief.tracking_url}"
        exact_message = f"Subject: {subject}\n\n{message_body}"

        distribution_execution_service.edit(
            brief.action_id,
            DistributionActionEditRequest(content_text=exact_message),
        )
        updated = brief.model_copy(
            update={
                "message_subject": subject,
                "message_body": message_body,
                "updated_at": datetime.now(UTC),
            }
        )
        self._persist(updated)
        return updated

    def reject(self, brief_id: UUID) -> OutreachBriefView:
        brief = self._reviewable_brief(brief_id, require_executable_target=False)
        distribution_execution_service.skip(brief.action_id)
        updated = brief.model_copy(
            update={
                "status": OutreachBriefStatus.REJECTED,
                "updated_at": datetime.now(UTC),
            }
        )
        self._persist(updated)
        return updated

    def _reviewable_brief(
        self,
        brief_id: UUID,
        *,
        require_executable_target: bool,
    ) -> OutreachBriefView:
        brief = outreach_brief_service.get(brief_id)
        if brief.status != OutreachBriefStatus.DRAFT:
            raise ValueError("Only DRAFT OutreachBrief objects can be reviewed")
        if require_executable_target:
            outreach_target_service.require_executable(brief.outreach_target_id)
        if outreach_sender_service.get_attempt(brief.id) is not None:
            raise ValueError("Outreach draft cannot change after an SMTP send attempt exists")
        action = distribution_execution_service.get_action(brief.action_id)
        if action.status != DistributionActionStatus.PREPARED:
            raise ValueError("Outreach action must be PREPARED before draft review")
        return brief

    def _persist(self, brief: OutreachBriefView) -> None:
        self._store.put(
            OUTREACH_BRIEF_NAMESPACE,
            str(brief.id),
            brief.model_dump(mode="json"),
        )


outreach_brief_review_service = OutreachBriefReviewService()
