from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.channel_execution import PublisherMode
from app.customer_channel_schemas import (
    CustomerStartingMoveDraftView,
    CustomerStartingMoveSetupView,
)
from app.customer_execution_requests import CustomerExecutionRequestService
from app.distribution_play_schemas import DistributionPlayView
from app.distribution_schemas import DistributionOpportunityView
from app.distribution_types import DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
PRODUCT_ID = UUID("22222222-2222-4222-8222-222222222222")
OTHER_PRODUCT_ID = UUID("33333333-3333-4333-8333-333333333333")
ICP_ID = UUID("44444444-4444-4444-8444-444444444444")
OPPORTUNITY_ID = UUID("55555555-5555-4555-8555-555555555555")
PLAY_ID = UUID("66666666-6666-4666-8666-666666666666")
OTHER_PLAY_ID = UUID("77777777-7777-4777-8777-777777777777")
SOURCE_URL = "https://www.reddit.com/r/freelance/comments/example/thread/"


def _draft() -> CustomerStartingMoveDraftView:
    return CustomerStartingMoveDraftView(
        project_id=PROJECT_ID,
        platform=DistributionPlatform.REDDIT,
        channel_label="Reddit",
        review_status="ACCEPTED",
        source_title="Freelancer bookkeeping discussion",
        source_url=SOURCE_URL,
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


def _setup() -> CustomerStartingMoveSetupView:
    return CustomerStartingMoveSetupView(
        project_id=PROJECT_ID,
        platform=DistributionPlatform.REDDIT,
        channel_label="Reddit",
        state="READY_FOR_HANDOFF",
        channel_mode="RESEARCH_ONLY",
        publisher_mode=PublisherMode.MANUAL,
        connected=False,
        execution_allowed=False,
        next_step="Manual handoff is ready.",
    )


def _play(
    *,
    play_id: UUID = PLAY_ID,
    product_id: UUID = PRODUCT_ID,
    platform: DistributionPlatform = DistributionPlatform.REDDIT,
    opportunity_id: UUID = OPPORTUNITY_ID,
    status: str = "READY",
) -> DistributionPlayView:
    return DistributionPlayView(
        id=play_id,
        product_id=product_id,
        icp_id=ICP_ID,
        opportunity_id=opportunity_id,
        platform=platform,
        opportunity_kind="SUBREDDIT" if platform == DistributionPlatform.REDDIT else "GROUP",
        opportunity_title="Freelancer bookkeeping discussion",
        tactic_id="community_helpful_reply",
        tactic_class="COMMUNITY",
        action_type="REPLY",
        automation_level="ASSISTED",
        attribution_level="ACTION",
        identity_required=False,
        community_policy_required=False,
        status=status,
        blockers=[] if status == "READY" else ["Operator review still required"],
        hypothesis="A useful bookkeeping reply will generate qualified downstream interest.",
        execution_steps=["Read the thread context", "Prepare a useful native reply"],
        success_metric="Qualified downstream interest",
        estimated_cost_min=0,
        estimated_cost_max=0,
        effort_hours=0.5,
        time_to_signal_days=3,
        priority_score=80,
        rationale=["Matches researched customer pain"],
    )


def _opportunity(
    *,
    opportunity_id: UUID = OPPORTUNITY_ID,
    url: str = SOURCE_URL,
    platform: DistributionPlatform = DistributionPlatform.REDDIT,
) -> DistributionOpportunityView:
    return DistributionOpportunityView(
        id=opportunity_id,
        icp_id=ICP_ID,
        platform=platform,
        kind="SUBREDDIT" if platform == DistributionPlatform.REDDIT else "GROUP",
        canonical_key="reddit:r/freelance:bookkeeping",
        title="Freelancer bookkeeping discussion",
        url=url,
        relevance_score=92,
        rationale="Exact researched source",
    )


def _requested_service() -> tuple[CustomerExecutionRequestService, MemoryRuntimeStateStore, UUID]:
    store = MemoryRuntimeStateStore()
    service = CustomerExecutionRequestService(store)
    request = service.request(
        project={"id": str(PROJECT_ID), "product_id": str(PRODUCT_ID)},
        draft=_draft(),
        setup=_setup(),
    )
    return service, store, request.id


def test_operator_can_link_only_validated_ready_play_without_creating_execution() -> None:
    service, store, request_id = _requested_service()

    linked = service.link_preparation(
        request_id=request_id,
        play=_play(),
        opportunity=_opportunity(),
    )

    assert linked.status == "PREPARATION_READY"
    assert linked.distribution_play_id == PLAY_ID
    assert linked.opportunity_id == OPPORTUNITY_ID
    assert linked.preparation_ready_at is not None
    assert linked.execution_allowed is False
    assert linked.customer_publish_confirmation_required is True
    assert store.list_namespace("distribution_action") == []
    assert store.list_namespace("distribution_experiment") == []


@pytest.mark.parametrize(
    ("play", "opportunity", "expected"),
    [
        (_play(status="BLOCKED"), _opportunity(), "Only a READY DistributionPlay"),
        (
            _play(product_id=OTHER_PRODUCT_ID),
            _opportunity(),
            "does not belong to the requested product",
        ),
        (
            _play(platform=DistributionPlatform.TELEGRAM),
            _opportunity(),
            "platform does not match the customer request",
        ),
        (
            _play(),
            _opportunity(url="https://www.reddit.com/r/freelance/comments/other/thread/"),
            "does not match the customer research source",
        ),
    ],
)
def test_preparation_link_fails_closed_on_domain_mismatch(
    play: DistributionPlayView,
    opportunity: DistributionOpportunityView,
    expected: str,
) -> None:
    service, _, request_id = _requested_service()

    with pytest.raises(ValueError, match=expected):
        service.link_preparation(
            request_id=request_id,
            play=play,
            opportunity=opportunity,
        )

    assert service.get_request(request_id).status == "REQUESTED"


def test_preparation_link_is_idempotent_but_refuses_relink() -> None:
    service, _, request_id = _requested_service()
    first = service.link_preparation(
        request_id=request_id,
        play=_play(),
        opportunity=_opportunity(),
    )
    second = service.link_preparation(
        request_id=request_id,
        play=_play(),
        opportunity=_opportunity(),
    )

    assert second == first

    with pytest.raises(ValueError, match="already linked to a different preparation play"):
        service.link_preparation(
            request_id=request_id,
            play=_play(play_id=OTHER_PLAY_ID),
            opportunity=_opportunity(),
        )
