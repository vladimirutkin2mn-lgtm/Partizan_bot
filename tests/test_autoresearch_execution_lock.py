from contextlib import contextmanager
from uuid import uuid4

import pytest

from app.autonomous_controlled_growth import AutonomousGrowthSweepAlreadyRunning
from app.growth_autoresearch_execution_runtime import (
    ResumableGrowthAutoResearchExecutionService,
)


class _DurableStoreStub:
    ephemeral = False


@contextmanager
def _denied_lock():
    yield False


@pytest.mark.asyncio
async def test_live_autoresearch_sweep_fails_closed_on_distributed_lock_contention() -> None:
    service = ResumableGrowthAutoResearchExecutionService(
        store=_DurableStoreStub(),
        sweep_lock_factory=_denied_lock,
    )

    with pytest.raises(AutonomousGrowthSweepAlreadyRunning, match="already running"):
        await service.run_once(product_id=uuid4())


@pytest.mark.asyncio
async def test_operator_trial_execution_uses_same_distributed_lock() -> None:
    service = ResumableGrowthAutoResearchExecutionService(
        store=_DurableStoreStub(),
        sweep_lock_factory=_denied_lock,
    )

    with pytest.raises(AutonomousGrowthSweepAlreadyRunning, match="already running"):
        await service.execute_trial(uuid4())
