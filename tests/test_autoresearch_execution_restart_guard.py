from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.growth_autoresearch_execution_runtime import (
    ResumableGrowthAutoResearchExecutionService,
)
from app.growth_autoresearch_schemas import (
    GrowthResearchTrialStatus,
    GrowthResearchTrialView,
    GrowthVariantSpec,
)
from app.runtime_store import get_runtime_store


class _AutoResearchStub:
    def __init__(self, trial: GrowthResearchTrialView) -> None:
        self._trial = trial

    def get_trial(self, trial_id):
        if trial_id != self._trial.id:
            raise KeyError(trial_id)
        return self._trial

    def get_policy(self, product_id):
        if product_id != self._trial.product_id:
            raise KeyError(product_id)
        return SimpleNamespace(paused=False)


@pytest.mark.asyncio
async def test_restart_does_not_repeat_trial_after_interrupted_preparation() -> None:
    product_id = uuid4()
    trial = GrowthResearchTrialView(
        id=uuid4(),
        product_id=product_id,
        champion_id=uuid4(),
        challenger=GrowthVariantSpec(
            platform="TIKTOK",
            tactic_id="tiktok_partizan_organic_video",
            message_angle="Test one bounded angle",
            test_budget=0,
        ),
        changed_dimensions=["message_angle"],
        status=GrowthResearchTrialStatus.READY,
        created_at=datetime.now(UTC),
    )
    store = get_runtime_store()
    service = ResumableGrowthAutoResearchExecutionService(
        store=store,
        autoresearch=_AutoResearchStub(trial),
    )

    # There is deliberately no DistributionPlay. The call fails after the persisted
    # PREPARING reservation but before any confirmed trial -> action linkage.
    with pytest.raises(KeyError):
        await service.execute_trial(trial.id)

    reserved = service.get_for_trial(trial.id)
    assert reserved is not None
    assert reserved.status == "PREPARING"
    assert reserved.action_id is None

    restarted = ResumableGrowthAutoResearchExecutionService(
        store=store,
        autoresearch=_AutoResearchStub(trial),
    )
    guarded = await restarted.execute_trial(trial.id)

    assert guarded.id == reserved.id
    assert guarded.status == "ERROR"
    assert guarded.action_id is None
    assert "duplicate external action" in guarded.reasons[0]

    again = await restarted.execute_trial(trial.id)
    assert again.id == guarded.id
    assert again.status == "ERROR"
