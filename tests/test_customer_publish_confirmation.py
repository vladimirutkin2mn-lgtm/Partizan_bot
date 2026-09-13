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
from app.customer_publish_confirmation import CustomerPublishConfirmationService
from app.distribution_execution_schemas import DistributionExecutionPrepareRequest
from app.distribution_execution_service import (
    DISTRIBUTION_ACTION_NAMESPACE,
    InMemoryDistributionExecutionService,
)
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


def _prepared(monkeypatch: pytest.MonkeyPatch):
    store = MemoryRuntimeStateStore()
    request_service = CustomerExecutionRequestService(store)
    execution_service = InMemoryDistributionExecutionService(store)
    draft = _draft()
    project = {"id": str(PROJECT_ID), "product_id": str(PRODUCT_ID)}
    requested = request_service.request(project=project, draft=draft, setup=_setup())
    linked = request_service.link_preparation(
        request_id=requested.id,
        play=_play(),
        opportunity=_opportunity(),
    )
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
    request_service.mark_action_prepared(request_id=linked.id, plan=plan)
    confirmation_service = CustomerPublishConfirmationService(
        request_service=request_service,
        execution_service=execution_service,
        store=store,
    )
    return store, request_service, execution_service, confirmation_service, project, draft, plan


def test_prepared_action_view_exposes_exact_locked_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    _, request_service, execution_service, confirmation_service, project, draft, plan = _prepared(
        monkeypatch
    )

    view = confirmation_service.view(project=project, draft=draft)

    assert view.distribution_action_id == plan.action.id
    assert str(view.target_url) == SOURCE_URL
    assert view.draft_title == TITLE
    assert view.context_text == CONTEXT
    assert view.content_text == CONTENT
    assert view.customer_publish_confirmed is False
    assert view.execution_allowed is False
    assert view.operator_approval_required is True
    assert view.published is False
    request = request_service.get_request(view.request_id)
    stored = execution_service.get_plan(plan.action.id)
    assert request.status == "ACTION_PREPARED"
    assert stored.action.status.value == "PREPARED"
    assert stored.experiment.status.value == "DRAFT"


def test_exact_confirmation_is_idempotent_and_keeps_action_prepared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, request_service, execution_service, confirmation_service, project, draft, plan = _prepared(
        monkeypatch
    )

    first = confirmation_service.confirm(project=project, draft=draft)
    first_request = request_service.get_request(first.request_id)
    second = confirmation_service.confirm(project=project, draft=draft)
    second_request = request_service.get_request(second.request_id)
    stored = execution_service.get_plan(plan.action.id)

    assert first.customer_publish_confirmed is True
    assert first.customer_publish_confirmed_at is not None
    assert second.customer_publish_confirmed_at == first.customer_publish_confirmed_at
    assert first_request.status == "PUBLISH_CONFIRMED"
    assert second_request.status == "PUBLISH_CONFIRMED"
    assert first_request.customer_publish_confirmation_fingerprint is not None
    assert len(first_request.customer_publish_confirmation_fingerprint) == 64
    assert (
        second_request.customer_publish_confirmation_fingerprint
        == first_request.customer_publish_confirmation_fingerprint
    )
    assert stored.action.status.value == "PREPARED"
    assert stored.experiment.status.value == "DRAFT"
    assert stored.action.operational_metadata["customer_publish_confirmed_at"]
    assert (
        stored.action.operational_metadata["customer_publish_confirmation_fingerprint"]
        == first_request.customer_publish_confirmation_fingerprint
    )
    assert len(execution_service.list_experiments(PRODUCT_ID)) == 1


def test_confirmation_rejects_changed_prepared_content_without_stamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request_service, _, _, project, draft, plan = _prepared(monkeypatch)
    tampered = plan.action.model_copy(update={"content_text": "Changed after customer review."})
    store.put(
        DISTRIBUTION_ACTION_NAMESPACE,
        str(tampered.id),
        tampered.model_dump(mode="json"),
    )
    fresh_execution_service = InMemoryDistributionExecutionService(store)
    confirmation_service = CustomerPublishConfirmationService(
        request_service=request_service,
        execution_service=fresh_execution_service,
        store=store,
    )

    with pytest.raises(ValueError, match="content no longer matches"):
        confirmation_service.confirm(project=project, draft=draft)

    request = request_service.get_request(plan.action.operational_metadata["customer_execution_request_id"])
    stored = fresh_execution_service.get_plan(plan.action.id)
    assert request.status == "ACTION_PREPARED"
    assert "customer_publish_confirmed_at" not in stored.action.operational_metadata
    assert "customer_publish_confirmation_fingerprint" not in stored.action.operational_metadata
    assert stored.action.status.value == "PREPARED"
    assert stored.experiment.status.value == "DRAFT"
