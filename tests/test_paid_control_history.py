from app.paid_control_sweep import (
    PAID_CONTROL_SWEEP_RUN_NAMESPACE,
    PaidControlSweepRegistry,
    PaidControlSweepService,
)
from app.runtime_store import MemoryRuntimeStateStore


def test_paid_control_sweep_history_is_persisted_and_bounded() -> None:
    store = MemoryRuntimeStateStore()
    service = PaidControlSweepService(
        store=store,
        registry=PaidControlSweepRegistry([]),
        history_retention=2,
    )

    first = service.run_once()
    second = service.run_once()
    third = service.run_once()

    recent = service.recent_runs(limit=2)
    assert [run.run_id for run in recent] == [third.run_id, second.run_id]
    persisted = store.list_namespace(PAID_CONTROL_SWEEP_RUN_NAMESPACE)
    assert len(persisted) == 2
    assert all(row["run_id"] != str(first.run_id) for row in persisted)


def test_paid_control_sweep_history_limit_is_guarded() -> None:
    service = PaidControlSweepService(
        store=MemoryRuntimeStateStore(),
        registry=PaidControlSweepRegistry([]),
        history_retention=3,
    )
    service.run_once()

    try:
        service.recent_runs(limit=4)
    except ValueError as exc:
        assert "between 1 and 3" in str(exc)
    else:
        raise AssertionError("recent_runs should reject limits above retention")
