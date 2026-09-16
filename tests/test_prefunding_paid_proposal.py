from __future__ import annotations

from uuid import uuid4

import pytest

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

PROJECT_ID = uuid4()
PRODUCT_ID = uuid4()


def _play(
    *,
    priority: float,
    cost: float,
    action_type: DistributionActionType = DistributionActionType.PAID_CAMPAIGN,
) -> DistributionPlayView:
    return DistributionPlayView(
        id=uuid4(),
        product_id=PRODUCT_ID,
        icp_id=uuid4(),
        opportunity_id=uuid4(),
        platform=DistributionPlatform.INSTAGRAM,
        opportunity_kind=OpportunityKind.CREATOR_ACCOUNT,
        opportunity_title=f"Paid opportunity {priority}",
        tactic_id=f"paid-{priority}",
        tactic_class=DistributionTacticClass.PAID_PLATFORM,
        action_type=action_type,
        automation_level=AutomationLevel.ASSISTED,
        attribution_level=AttributionLevel.CAMPAIGN,
        identity_required=False,
        status=DistributionPlayStatus.READY,
        hypothesis="A concrete paid placement can produce qualified acquisition signal.",
        execution_steps=["Create the smallest test.", "Measure paid conversion signal."],
        success_metric="Paid conversions",
        estimated_cost_min=1,
        estimated_cost_max=cost,
        effort_hours=1,
        time_to_signal_days=3,
        priority_score=priority,
    )


def test_proposal_is_deterministic_idempotent_and_budget_bounded() -> None:
    service = PrefundingPaidProposalService(MemoryRuntimeStateStore())
    low = _play(priority=10, cost=8)
    high = _play(priority=90, cost=25)

    first = service.get_or_create(
        project_id=PROJECT_ID,
        product_id=PRODUCT_ID,
        project_budget_usd=15,
        plays=[low, high],
    )
    second = service.get_or_create(
        project_id=PROJECT_ID,
        product_id=PRODUCT_ID,
        project_budget_usd=15,
        plays=[low, high],
    )

    assert first.id == second.id
    assert first.play_id == high.id
    assert first.budget_cap == 15
    assert first.project_id == PROJECT_ID
    assert first.product_id == PRODUCT_ID


def test_proposal_does_not_accept_non_paid_or_blocked_plays() -> None:
    service = PrefundingPaidProposalService(MemoryRuntimeStateStore())
    organic = _play(
        priority=100,
        cost=10,
        action_type=DistributionActionType.STANDALONE_POST,
    )
    blocked = _play(priority=90, cost=10).model_copy(
        update={
            "status": DistributionPlayStatus.BLOCKED,
            "blockers": ["provider unavailable"],
        }
    )

    with pytest.raises(ValueError, match="No executable paid distribution play"):
        service.get_or_create(
            project_id=PROJECT_ID,
            product_id=PRODUCT_ID,
            project_budget_usd=20,
            plays=[organic, blocked],
        )


def test_proposal_rejects_zero_customer_test_budget() -> None:
    service = PrefundingPaidProposalService(MemoryRuntimeStateStore())

    with pytest.raises(ValueError, match="Customer test budget must be positive"):
        service.get_or_create(
            project_id=PROJECT_ID,
            product_id=PRODUCT_ID,
            project_budget_usd=0,
            plays=[_play(priority=100, cost=10)],
        )


def test_proposal_is_project_bound() -> None:
    service = PrefundingPaidProposalService(MemoryRuntimeStateStore())
    proposal = service.get_or_create(
        project_id=PROJECT_ID,
        product_id=PRODUCT_ID,
        project_budget_usd=20,
        plays=[_play(priority=100, cost=10)],
    )

    with pytest.raises(ValueError, match="does not belong"):
        service.require_project(proposal.id, uuid4())
