from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.autonomous_controlled_growth import AutonomousControlledGrowthSweepService
from app.autonomy_schemas import GrowthMandateStatus, GrowthMandateView
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore


class AllowRunningControl:
    def evaluate_running(self, mandate: GrowthMandateView) -> None:
        return None


class SuppressedTargetRaceSendService:
    async def run_next(self, product_id):
        raise ValueError("OutreachTarget is SUPPRESSED: OPERATOR_SUPPRESSED")


@pytest.mark.asyncio
async def test_autonomous_outreach_fails_closed_when_target_changes_before_send() -> None:
    now = datetime.now(UTC)
    mandate = GrowthMandateView(
        id=uuid4(),
        product_id=uuid4(),
        version=3,
        status=GrowthMandateStatus.ACTIVE,
        total_budget_cap=1000,
        target_max_cac=12,
        max_autonomous_spend_per_experiment=0,
        max_autonomous_spend_per_day=0,
        max_concurrent_running_experiments=5,
        allowed_platforms=[DistributionPlatform.REDDIT],
        allowed_actions=[DistributionActionType.OUTREACH_EMAIL],
        autonomous_prepare=True,
        autonomous_approve=False,
        autonomous_paid_activation=False,
        approval_threshold=None,
        effective_from=None,
        effective_until=None,
        created_at=now,
        updated_at=now,
    )
    service = AutonomousControlledGrowthSweepService(
        store=MemoryRuntimeStateStore(),
        control_service=AllowRunningControl(),
        outreach_send_service=SuppressedTargetRaceSendService(),
    )

    decisions = await service._run_product(uuid4(), mandate)

    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.outcome.value == "BLOCKED"
    assert decision.evaluation_decision.value == "BLOCK"
    assert decision.action_type == "OUTREACH_EMAIL"
    assert "failed closed" in " ".join(decision.reasons).lower()
    assert "suppressed" in " ".join(decision.reasons).lower()
