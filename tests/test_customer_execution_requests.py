from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.channel_execution import PublisherMode
from app.customer_channel_schemas import (
    CustomerStartingMoveDraftView,
    CustomerStartingMoveSetupView,
)
from app.customer_execution_requests import CustomerExecutionRequestService
from app.distribution_types import DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("66666666-6666-4666-8666-666666666666")
PRODUCT_ID = UUID("77777777-7777-4777-8777-777777777777")


def _draft(status: str = "ACCEPTED") -> CustomerStartingMoveDraftView:
    return CustomerStartingMoveDraftView(
        project_id=PROJECT_ID,
        platform=DistributionPlatform.REDDIT,
        channel_label="Reddit",
        review_status=status,
        source_title="Freelancer bookkeeping discussion",
        source_url="https://www.reddit.com/r/freelance/comments/example/thread/",
        title="Useful bookkeeping reply",
        context_text="Freelancers are comparing recurring bookkeeping workflow pain.",
        content_text="Share a useful bookkeeping workflow perspective without a product link.",
        rationale="Grounded in researched evidence.",
        signal_to_watch="Useful replies and downstream interest",
        execution_allowed=False,
        execution_requirement="Accepted for setup only; execution remains separate.",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _setup(state: str = "READY_FOR_HANDOFF") -> CustomerStartingMoveSetupView:
    return CustomerStartingMoveSetupView(
        project_id=PROJECT_ID,
        platform=DistributionPlatform.REDDIT,
        channel_label="Reddit",
        state=state,
        channel_mode="RESEARCH_ONLY",
        publisher_mode=PublisherMode.MANUAL,
        connected=False,
        execution_allowed=False,
        next_step="Manual handoff is ready.",
    )


def _project() -> dict:
    return {"id": str(PROJECT_ID), "product_id": str(PRODUCT_ID)}


def test_request_is_explicit_non_executing_and_idempotent() -> None:
    service = CustomerExecutionRequestService(MemoryRuntimeStateStore())

    first = service.request(project=_project(), draft=_draft(), setup=_setup())
    second = service.request(project=_project(), draft=_draft(), setup=_setup())

    assert second.id == first.id
    assert first.status == "REQUESTED"
    assert first.project_id == PROJECT_ID
    assert first.product_id == PRODUCT_ID
    assert first.platform == DistributionPlatform.REDDIT
    assert first.publisher_mode == PublisherMode.MANUAL
    assert first.execution_allowed is False
    assert first.customer_publish_confirmation_required is True
    assert len(service.list_requests()) == 1


@pytest.mark.parametrize(
    ("draft_status", "setup_state", "expected"),
    [
        ("DRAFT", "READY_FOR_HANDOFF", "Accept the starting-move draft"),
        ("ACCEPTED", "NEEDS_SETUP", "Complete the required channel setup"),
        ("ACCEPTED", "UNAVAILABLE", "Complete the required channel setup"),
    ],
)
def test_request_fails_closed_before_handoff_is_ready(
    draft_status: str,
    setup_state: str,
    expected: str,
) -> None:
    service = CustomerExecutionRequestService(MemoryRuntimeStateStore())

    with pytest.raises(ValueError, match=expected):
        service.request(
            project=_project(),
            draft=_draft(draft_status),
            setup=_setup(setup_state),
        )

    assert service.list_requests() == []


def test_view_only_returns_request_for_current_accepted_draft() -> None:
    service = CustomerExecutionRequestService(MemoryRuntimeStateStore())
    request = service.request(project=_project(), draft=_draft(), setup=_setup())

    assert service.view(project=_project(), draft=_draft()).id == request.id
    assert service.view(project=_project(), draft=_draft("DRAFT")) is None
