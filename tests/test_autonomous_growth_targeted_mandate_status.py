import asyncio
from uuid import UUID

from app.autonomous_growth import AutonomousGrowthSweepService
from app.autonomy_schemas import GrowthMandateStatus, GrowthMandateUpsertRequest
from app.autonomy_service import GrowthMandateService
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore

ACTIVE_PRODUCT_ID = UUID("11111111-1111-1111-1111-111111111111")
PAUSED_PRODUCT_ID = UUID("22222222-2222-2222-2222-222222222222")
REVOKED_PRODUCT_ID = UUID("33333333-3333-3333-3333-333333333333")


class RecordingSweep(AutonomousGrowthSweepService):
    def __init__(self, *, store, mandate_service) -> None:
        super().__init__(store=store, mandate_service=mandate_service)
        self.processed: list[UUID] = []

    async def _run_product(self, run_id, mandate):
        self.processed.append(mandate.product_id)
        return []


def _mandate_request() -> GrowthMandateUpsertRequest:
    return GrowthMandateUpsertRequest(
        total_budget_cap=100,
        target_max_cac=10,
        max_autonomous_spend_per_experiment=20,
        max_autonomous_spend_per_day=50,
        max_concurrent_running_experiments=2,
        allowed_platforms=[DistributionPlatform.REDDIT],
        allowed_actions=[DistributionActionType.REPLY],
        autonomous_prepare=True,
        autonomous_approve=True,
        autonomous_paid_activation=False,
    )


def _services():
    store = MemoryRuntimeStateStore()
    mandates = GrowthMandateService(store)
    sweep = RecordingSweep(store=store, mandate_service=mandates)
    return mandates, sweep


def test_targeted_sweep_processes_active_mandate() -> None:
    mandates, sweep = _services()
    mandates.upsert(ACTIVE_PRODUCT_ID, _mandate_request())

    result = asyncio.run(sweep.run_once(product_id=ACTIVE_PRODUCT_ID))

    assert result.product_count == 1
    assert sweep.processed == [ACTIVE_PRODUCT_ID]


def test_targeted_sweep_skips_paused_mandate_before_product_or_provider_work() -> None:
    mandates, sweep = _services()
    mandates.upsert(PAUSED_PRODUCT_ID, _mandate_request())
    mandates.set_status(PAUSED_PRODUCT_ID, GrowthMandateStatus.PAUSED)

    result = asyncio.run(sweep.run_once(product_id=PAUSED_PRODUCT_ID))

    assert result.product_count == 0
    assert result.decision_count == 0
    assert sweep.processed == []


def test_targeted_sweep_skips_revoked_mandate() -> None:
    mandates, sweep = _services()
    mandates.upsert(REVOKED_PRODUCT_ID, _mandate_request())
    mandates.set_status(REVOKED_PRODUCT_ID, GrowthMandateStatus.REVOKED)

    result = asyncio.run(sweep.run_once(product_id=REVOKED_PRODUCT_ID))

    assert result.product_count == 0
    assert result.decision_count == 0
    assert sweep.processed == []


def test_untargeted_and_targeted_sweeps_share_active_only_admission() -> None:
    mandates, sweep = _services()
    mandates.upsert(ACTIVE_PRODUCT_ID, _mandate_request())
    mandates.upsert(PAUSED_PRODUCT_ID, _mandate_request())
    mandates.set_status(PAUSED_PRODUCT_ID, GrowthMandateStatus.PAUSED)

    result = asyncio.run(sweep.run_once())

    assert result.product_count == 1
    assert sweep.processed == [ACTIVE_PRODUCT_ID]
