from uuid import uuid4

from app.distribution_play_schemas import (
    DistributionPlayStatus,
    DistributionPlayView,
    DistributionTacticClass,
)
from app.distribution_types import (
    AttributionLevel,
    AutomationLevel,
    DistributionActionType,
    DistributionPlatform,
    OpportunityKind,
)
from app.prefunding_paid_proposal import PrefundingPaidProposalService
from app.runtime_store import MemoryRuntimeStateStore


def _paid_play(product_id, *, priority: float = 90.0) -> DistributionPlayView:
    return DistributionPlayView(
        id=uuid4(),
        product_id=product_id,
        icp_id=uuid4(),
        opportunity_id=uuid4(),
        platform=DistributionPlatform.INSTAGRAM,
        opportunity_kind=OpportunityKind.CREATOR_ACCOUNT,
        opportunity_title="Paid audience",
        tactic_id="instagram_ads",
        tactic_class=DistributionTacticClass.PAID_PLATFORM,
        action_type=DistributionActionType.PAID_CAMPAIGN,
        automation_level=AutomationLevel.APPROVAL_GATED,
        attribution_level=AttributionLevel.PAID,
        identity_required=False,
        status=DistributionPlayStatus.READY,
        hypothesis="A bounded paid test can produce a measurable acquisition signal.",
        execution_steps=["Review target.", "Run bounded test after approval."],
        success_metric="first paid customer",
        estimated_cost_min=100.0,
        estimated_cost_max=500.0,
        effort_hours=2.5,
        time_to_signal_days=5,
        priority_score=priority,
    )


def test_same_paid_play_and_budget_reuses_existing_proposal() -> None:
    service = PrefundingPaidProposalService(MemoryRuntimeStateStore())
    project_id = uuid4()
    product_id = uuid4()
    play = _paid_play(product_id)

    first = service.get_or_create(
        project_id=project_id,
        product_id=product_id,
        project_budget_usd=50.0,
        plays=[play],
    )
    second = service.get_or_create(
        project_id=project_id,
        product_id=product_id,
        project_budget_usd=50.0,
        plays=[play],
    )

    assert second.id == first.id
    assert second.play_id == play.id
    assert second.budget_cap == 50.0


def test_changed_recommended_play_creates_new_proposal() -> None:
    service = PrefundingPaidProposalService(MemoryRuntimeStateStore())
    project_id = uuid4()
    product_id = uuid4()
    first_play = _paid_play(product_id)
    next_play = _paid_play(product_id)

    first = service.get_or_create(
        project_id=project_id,
        product_id=product_id,
        project_budget_usd=50.0,
        plays=[first_play],
    )
    refreshed = service.get_or_create(
        project_id=project_id,
        product_id=product_id,
        project_budget_usd=50.0,
        plays=[next_play],
    )

    assert refreshed.id != first.id
    assert refreshed.play_id == next_play.id


def test_changed_project_budget_creates_new_proposal_for_same_play() -> None:
    service = PrefundingPaidProposalService(MemoryRuntimeStateStore())
    project_id = uuid4()
    product_id = uuid4()
    play = _paid_play(product_id)

    first = service.get_or_create(
        project_id=project_id,
        product_id=product_id,
        project_budget_usd=50.0,
        plays=[play],
    )
    refreshed = service.get_or_create(
        project_id=project_id,
        product_id=product_id,
        project_budget_usd=40.0,
        plays=[play],
    )

    assert refreshed.id != first.id
    assert refreshed.play_id == play.id
    assert refreshed.budget_cap == 40.0
