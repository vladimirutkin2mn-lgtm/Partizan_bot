from types import SimpleNamespace
from uuid import uuid4

from app.autonomous_growth_worker import AutonomousGrowthWorker
from app.runtime_store import MemoryRuntimeStateStore


class FakeRefreshService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, int]] = []

    async def run_once(self, *, product_id=None, interval_seconds: int):
        self.calls.append((product_id, interval_seconds))
        return []


class FakeAsyncService:
    async def run_once(self, *, product_id=None):
        return SimpleNamespace()


class FakeSweepService:
    def __init__(self) -> None:
        self.store = MemoryRuntimeStateStore()
        self.calls = 0

    async def run_once(self, product_id=None):
        self.calls += 1
        return SimpleNamespace(model_dump_json=lambda: "{}")


def test_one_shot_worker_refreshes_opportunities_before_execution_sweep() -> None:
    refresh = FakeRefreshService()
    sweep = FakeSweepService()
    worker = AutonomousGrowthWorker(
        sweep_service=sweep,
        autoresearch_execution_service=FakeAsyncService(),
        autoresearch_loop_service=FakeAsyncService(),
        opportunity_refresh_service=refresh,
    )
    product_id = uuid4()

    result = worker.run(
        once=True,
        interval_seconds=300,
        discovery_interval_seconds=21600,
        product_id=product_id,
        emit=lambda _: None,
    )

    assert result == 0
    assert refresh.calls == [(product_id, 21600)]
    assert sweep.calls == 1
