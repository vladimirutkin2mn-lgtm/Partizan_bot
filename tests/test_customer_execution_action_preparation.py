from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

import app.distribution_execution_service as distribution_execution_module
from app.channel_execution import PublisherMode
from app.customer_channel_schemas import (
    CustomerStartingMoveDraftView,
    CustomerStartingMoveSetupView,
)
from app.customer_execution_requests import CustomerExecutionRequestService
from app.distribution_execution_schemas import (
    DistributionActionEditRequest,
    DistributionExecutionPrepareRequest,
)
from app.distribution_execution_service import InMemoryDistributionExecutionService
from app.distribution_play_schemas import (
    DistributionPlayStatus,
    DistributionPlayView,
    DistributionTacticClass,
)
from app.distribution_schemas import DistributionOpportunityView
from app.distribution_types import (
    AttributionLevel,
    AutomationLevel,
    DistributionActionType,
    DistributionPlatform,
    OpportunityKind,
)
from app.models import ProductProfileStatus
from app.runtime_store import MemoryRuntimeStateStore
from app.schemas import ProductProfileView

PROJECT_ID = UUID("11111111-2222-4333-8444-555555555555")
PRODUCT_ID = UUID("22222222-3333-4444-8555-666666666666")
ICP_ID = UUID("33333333-4444-4555-8666-777777777777")
OPPORTUNITY_ID = UUID("44444444-5555-4666-8777-888888888888")
PLAY_ID = UUID("55555555-6666-4777-8888-999999999999")
SOURCE_URL = "https://www.reddit.com/r/freelance/comments/example/thread/"
CONTEXT = "Freelancers are comparing recurring bookkeeping workflow pain."
CONTENT = "Share a useful bookkeeping workflow perspective without a product link."
TITLE = "Useful bookkeeping reply"


def _draft() -> CustomerStartingMoveDraftView:
    now = datetime.now(UTC)
    return CustomerStartingMoveDraftView(
        project_id=PROJECT_ID,
        platform=DistributionPlatform.REDDIT,
        channel_label="Reddit",
        review_status="ACCEPTED",
        source_title="Freelancer bookkeeping discussion",
        source_url=SOURCE_URL,
        title=TITLE,
        context_text=CONTEXT,
        content_text=CONTENT,
        rationale="Grounded in researched evidence.",
        signal_to_watch="Useful replies and downstream interest",
        execution_allowed=False,
        execution_requirement="Accepted for setup only; execution remains separate.",
        created_at=now,
        updated_at=now,
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


def _product() -> ProductProfileView:
    return ProductProfileView(
        id=PRODUCT_ID,
        input_brief="Bookkeeping workflow product for freelancers.",
        name="Ledger Helper",
        description="Helps freelancers keep recurring bookkeeping tasks organized.",
        problem_or_desire="Bookkeeping is repetitive and easy to postpone.",
        value_proposition="A calmer recurring bookkeeping workflow.",
        usp=None,
        use_cases=["Recurring bookkeeping workflow"],
        market="US",
        language="English",
        price=None,
        pricing_model=None,
        goal="Validate demand",
        budget=0,
        max_cac=None,
        allowed_channels=["REDDIT"],
        constraints=[],
        known_audience=["Freelancers"],
        known_competitors=[],
        reference_links=["https://example.com/ledger-helper"],
        assumptions=[],
        contradictions=[],
        status=ProductProfileStatus.CONFIRMED,
    )


def _opportunity() -> DistributionOpportunityView:
    return DistributionOpportunityView(
        id=OPPORTUNITY_ID,
        icp_id=ICP_ID,
        platform=DistributionPlatform.REDDIT,
        kind=OpportunityKind.SUBREDDIT,
        canonical_key="reddit-freelance-thread",
        title="Freelancer bookkeeping discussion",
        url=SOURCE_URL,
        relevance_score=92,
        rationale="Strong match",
        evidence=[{"title": "Thread", "url": SOURCE_URL}],
    )


def _play() -> DistributionPlayView:
    return DistributionPlayView(
        id=PLAY_ID,
        product_id=PRODUCT_ID,
        icp_id=ICP_ID,
        opportunity_id=OPPORTUNITY_ID,
        platform=DistributionPlatform.REDDIT,
        opportunity_kind=OpportunityKind.SUBREDDIT,
        opportunity_title="Freelancer bookkeeping discussion",
        tactic_id="reddit_value_reply",
        tactic_class=DistributionTacticClass.COMMUNITY,
        action_type=DistributionActionType.REPLY,
        automation_level=AutomationLevel.MANUAL,
        attribution_level=AttributionLevel.ACTION,
        identity_required=False,
        community_policy_required=False,
        status=DistributionPlayStatus.READY,
        blockers=[],
        hypothesis="A useful value-first reply can produce a measurable demand signal.",
        execution_steps=["Review exact reply", "Publish only after final confirmation"],
        success_metric="Qualified replies",
        estimated_cost_min=0,
        estimated_cost_max=0,
        effort_hours=0.5,
        time_to_signal_days=3,
        priority_score=80,
        rationale=["Evidence-backed customer request"],
    )


def _requested_and_linked() -> tuple[CustomerExecutionRequestService, object]:
    service = CustomerExecutionRequestService(MemoryRuntimeStateStore())
    request = service.request(
        project={"id": str(PROJECT_ID), "product_id": str(PRODUCT_ID)},
        draft=_draft(),
        setup=_setup(),
    )
    linked = service.link_preparation(
        request_id=request.id,
        play=_play(),
        opportunity=_opportunity(),
    )
    return service, linked


def test_customer_request_prepares_exact_locked_action_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_service, linked = _requested_and_linked()
    execution_service = InMemoryDistributionExecutionService(MemoryRuntimeStateStore())
    opportunity = _opportunity()
    monkeypatch.setattr(
        distribution_execution_module,
        "audience_intelligence_service",
        SimpleNamespace(find_opportunity=lambda opportunity_id: opportunity),
    )

    plan = execution_service.prepare(
        _product(),
        _play(),
        DistributionExecutionPrepareRequest(
            target_url=linked.source_url,
            title=linked.draft_title,
            context_text=linked.context_text,
            content_text=linked.content_text,
        ),
        customer_execution_request_id=linked.id,
    )
    prepared = request_service.mark_action_prepared(request_id=linked.id, plan=plan)
    repeated = request_service.mark_action_prepared(request_id=linked.id, plan=plan)

    assert repeated.id == prepared.id
    assert prepared.status == "ACTION_PREPARED"
    assert prepared.distribution_action_id == plan.action.id
    assert prepared.experiment_id == plan.experiment.id
    assert prepared.execution_allowed is False
    assert prepared.customer_publish_confirmation_required is True
    assert plan.action.status.value == "PREPARED"
    assert plan.experiment.status.value == "DRAFT"
    assert str(plan.action.target_url) == SOURCE_URL
    assert plan.action.content_text == CONTENT
    assert plan.action.content_payload["context_text"] == CONTEXT
    assert plan.action.content_payload["title"] == TITLE
    assert plan.action.operational_metadata["customer_execution_request_id"] == str(linked.id)
    assert plan.action.operational_metadata["customer_exact_content_locked"] is True
    assert plan.action.operational_metadata["customer_publish_confirmation_required"] is True
    assert "customer_publish_confirmed_at" not in plan.action.operational_metadata
    assert len(execution_service.list_experiments(PRODUCT_ID)) == 1


def test_customer_prepared_action_cannot_be_edited_or_approved_before_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, linked = _requested_and_linked()
    execution_service = InMemoryDistributionExecutionService(MemoryRuntimeStateStore())
    opportunity = _opportunity()
    monkeypatch.setattr(
        distribution_execution_module,
        "audience_intelligence_service",
        SimpleNamespace(find_opportunity=lambda opportunity_id: opportunity),
    )
    plan = execution_service.prepare(
        _product(),
        _play(),
        DistributionExecutionPrepareRequest(
            target_url=linked.source_url,
            title=linked.draft_title,
            context_text=linked.context_text,
            content_text=linked.content_text,
        ),
        customer_execution_request_id=linked.id,
    )

    with pytest.raises(ValueError, match="content is locked"):
        execution_service.edit(
            plan.action.id,
            DistributionActionEditRequest(content_text="Operator changed the accepted copy."),
        )
    with pytest.raises(ValueError, match="Customer publish confirmation"):
        execution_service.approve(plan.action.id)

    stored = execution_service.get_plan(plan.action.id)
    assert stored.action.status.value == "PREPARED"
    assert stored.experiment.status.value == "DRAFT"
    assert stored.action.content_text == CONTENT


def test_prepared_plan_validation_rejects_changed_customer_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_service, linked = _requested_and_linked()
    execution_service = InMemoryDistributionExecutionService(MemoryRuntimeStateStore())
    opportunity = _opportunity()
    monkeypatch.setattr(
        distribution_execution_module,
        "audience_intelligence_service",
        SimpleNamespace(find_opportunity=lambda opportunity_id: opportunity),
    )
    plan = execution_service.prepare(
        _product(),
        _play(),
        DistributionExecutionPrepareRequest(
            target_url=linked.source_url,
            title=linked.draft_title,
            context_text=linked.context_text,
            content_text=linked.content_text,
        ),
        customer_execution_request_id=linked.id,
    )
    changed_action = plan.action.model_copy(update={"content_text": "Changed after acceptance."})
    changed_plan = plan.model_copy(update={"action": changed_action})

    with pytest.raises(ValueError, match="exactly match the accepted customer draft"):
        request_service.mark_action_prepared(request_id=linked.id, plan=changed_plan)

    assert request_service.get_request(linked.id).status == "PREPARATION_READY"
