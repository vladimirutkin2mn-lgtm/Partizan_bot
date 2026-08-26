from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.execution_adapters import AdapterExecutionOutcome
from app.growth_autoresearch_execution_runtime import (
    ResumableGrowthAutoResearchExecutionService,
    growth_autoresearch_execution_runtime_service,
)
from app.growth_autoresearch_execution_schemas import GrowthAutoResearchExecutionStatus
from app.growth_autoresearch_schemas import (
    GrowthResearchTrialStatus,
    GrowthResearchTrialView,
    GrowthVariantSpec,
)
from app.organic_creative_execution import (
    organic_creative_distribution_execution_adapter_service,
)
from app.runtime_store import get_runtime_store


class _AutoResearchStub:
    def __init__(self, trial, *, paused: bool) -> None:
        self._trial = trial
        self._paused = paused

    def get_trial(self, trial_id):
        if trial_id != self._trial.id:
            raise KeyError(trial_id)
        return self._trial

    def get_policy(self, product_id):
        if product_id != self._trial.product_id:
            raise KeyError(product_id)
        return SimpleNamespace(paused=self._paused)


class _ReconciliationAdapterStub:
    def __init__(self, action_id, experiment_id) -> None:
        self.action_id = action_id
        self.experiment_id = experiment_id
        self.retries: list[bool] = []

    def execute(self, action_id, payload):
        assert action_id == self.action_id
        self.retries.append(payload.retry)
        return SimpleNamespace(
            receipt=SimpleNamespace(
                outcome=AdapterExecutionOutcome.IN_PROGRESS,
                message="Provider status is still processing.",
            ),
            plan=SimpleNamespace(
                action=SimpleNamespace(id=self.action_id),
                experiment=SimpleNamespace(id=self.experiment_id),
            ),
        )


def _trial():
    return GrowthResearchTrialView(
        id=uuid4(),
        product_id=uuid4(),
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


@pytest.mark.asyncio
async def test_in_progress_tiktok_trial_uses_retry_for_read_only_reconciliation_even_when_paused() -> None:
    trial = _trial()
    action_id = uuid4()
    experiment_id = uuid4()
    adapter = _ReconciliationAdapterStub(action_id, experiment_id)
    service = ResumableGrowthAutoResearchExecutionService(
        store=get_runtime_store(),
        autoresearch=_AutoResearchStub(trial, paused=True),
        adapter_service=adapter,
    )
    existing = service._record(
        trial=trial,
        status=GrowthAutoResearchExecutionStatus.IN_PROGRESS,
        platform="TIKTOK",
        action_type="ORGANIC_VIDEO",
        action_id=action_id,
        experiment_id=experiment_id,
        adapter_outcome="IN_PROGRESS",
        reasons=["Provider accepted the post and is processing it."],
    )

    result = await service.execute_trial(trial.id)

    assert result.id == existing.id
    assert result.status == "IN_PROGRESS"
    assert result.adapter_outcome == "IN_PROGRESS"
    assert adapter.retries == [True]


def test_production_autoresearch_runtime_uses_permissioned_organic_adapter_stack() -> None:
    assert (
        growth_autoresearch_execution_runtime_service._adapter_service
        is organic_creative_distribution_execution_adapter_service
    )
