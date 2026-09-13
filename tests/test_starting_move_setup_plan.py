from datetime import UTC, datetime
from uuid import UUID

from app.channel_execution import ChannelCapability, PublisherMode
from app.customer_channel_schemas import (
    CustomerChannelCapabilityView,
    CustomerChannelView,
    CustomerStartingMoveDraftView,
)
from app.customer_starting_move_setup import customer_starting_move_setup_service
from app.distribution_types import DistributionPlatform

PROJECT_ID = UUID("44444444-4444-4444-8444-444444444444")


def _draft(status: str = "ACCEPTED") -> CustomerStartingMoveDraftView:
    return CustomerStartingMoveDraftView(
        project_id=PROJECT_ID,
        platform=DistributionPlatform.REDDIT,
        channel_label="Reddit",
        review_status=status,
        source_title="Freelancer bookkeeping discussion",
        source_url="https://www.reddit.com/r/freelance/",
        title="Useful bookkeeping reply",
        context_text="Freelancers are comparing recurring bookkeeping workflow pain.",
        content_text="Share a useful bookkeeping workflow perspective without a product link.",
        rationale="Grounded in the researched Reddit opportunity.",
        signal_to_watch="Useful replies and downstream interest",
        execution_allowed=False,
        execution_requirement="Review only; execution remains separately controlled.",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _channel(
    *,
    publisher_mode: PublisherMode = PublisherMode.MANUAL,
    connected: bool | None = False,
    publish_ready: bool = False,
    publish_blocker: str | None = "select Client-owned as the Reddit publisher mode",
) -> CustomerChannelView:
    return CustomerChannelView(
        platform=DistributionPlatform.REDDIT,
        label="Reddit",
        mode="RESEARCH_ONLY",
        selected=True,
        publisher_mode=publisher_mode,
        capabilities=[
            CustomerChannelCapabilityView(capability=ChannelCapability.SEARCH, ready=True),
            CustomerChannelCapabilityView(
                capability=ChannelCapability.DRAFT,
                ready=False,
                blocker="channel-native draft adapter is not implemented yet",
            ),
            CustomerChannelCapabilityView(
                capability=ChannelCapability.PUBLISH,
                ready=publish_ready,
                blocker=None if publish_ready else publish_blocker,
            ),
            CustomerChannelCapabilityView(
                capability=ChannelCapability.MEASURE,
                ready=False,
                blocker="channel outcome adapter is not implemented yet",
            ),
        ],
        autonomous_execution_available=False,
        execution_ready=False,
        execution_blocker="autonomous execution is not supported for this channel",
        connected=connected,
    )


def test_accepted_manual_draft_returns_safe_handoff_plan() -> None:
    draft = _draft()
    channel = _channel()

    plan = customer_starting_move_setup_service.view(
        project_id=PROJECT_ID,
        draft=draft,
        channel=channel,
    )

    assert plan is not None
    assert plan.state == "READY_FOR_HANDOFF"
    assert plan.review_status == "ACCEPTED"
    assert plan.platform == DistributionPlatform.REDDIT
    assert plan.publisher_mode == PublisherMode.MANUAL
    assert plan.execution_allowed is False
    assert "no execution permission" in plan.next_step
    steps = {step.key: step for step in plan.steps}
    assert steps["REVIEW"].state == "READY"
    assert steps["CONNECTION"].state == "READY"
    assert "No account connection is required" in steps["CONNECTION"].detail
    assert steps["PUBLISH"].state == "READY"
    assert "without granting Partizan publish permission" in steps["PUBLISH"].detail


def test_client_owned_draft_reports_missing_connection_without_granting_execution() -> None:
    plan = customer_starting_move_setup_service.view(
        project_id=PROJECT_ID,
        draft=_draft(),
        channel=_channel(
            publisher_mode=PublisherMode.CLIENT_OWNED,
            connected=False,
            publish_blocker="connect an authorised Reddit account first",
        ),
    )

    assert plan is not None
    assert plan.state == "NEEDS_SETUP"
    assert plan.execution_allowed is False
    steps = {step.key: step for step in plan.steps}
    assert steps["CONNECTION"].state == "NEEDS_ACTION"
    assert steps["PUBLISH"].state == "NEEDS_ACTION"
    assert "complete the Reddit account connection" in plan.next_step


def test_setup_plan_exists_only_after_customer_accepts_review_draft() -> None:
    assert (
        customer_starting_move_setup_service.view(
            project_id=PROJECT_ID,
            draft=_draft("DRAFT"),
            channel=_channel(),
        )
        is None
    )
    assert (
        customer_starting_move_setup_service.view(
            project_id=PROJECT_ID,
            draft=_draft("REJECTED"),
            channel=_channel(),
        )
        is None
    )


def test_setup_service_has_no_execution_or_persistence_dependencies() -> None:
    source = __import__("inspect").getsource(
        __import__("app.customer_starting_move_setup", fromlist=["*"])
    )

    assert "distribution_execution" not in source
    assert "RuntimeStateStore" not in source
    assert ".put(" not in source
