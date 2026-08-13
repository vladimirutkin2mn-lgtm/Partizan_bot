from uuid import UUID

from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import DistributionActionStatus
from app.outreach_briefs import (
    OUTREACH_BRIEF_NAMESPACE,
    OutreachBriefStatus,
    outreach_brief_service,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store


class OutreachAutoSendLifecycleService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def finalize_rejected(self, brief_id: UUID) -> None:
        brief = outreach_brief_service.get(brief_id)
        action = distribution_execution_service.get_action(brief.action_id)
        if action.status in {
            DistributionActionStatus.PREPARED,
            DistributionActionStatus.APPROVED,
        }:
            distribution_execution_service.skip(action.id)
        rejected = brief.model_copy(
            update={"status": OutreachBriefStatus.REJECTED}
        )
        self._store.put(
            OUTREACH_BRIEF_NAMESPACE,
            str(brief.id),
            rejected.model_dump(mode="json"),
        )


outreach_autosend_lifecycle_service = OutreachAutoSendLifecycleService()
