from __future__ import annotations

from types import SimpleNamespace

import app.autonomous_growth_worker as worker_module
from app.autonomous_growth_worker import AutonomousGrowthWorker
from app.runtime_store import MemoryRuntimeStateStore


class FakeAsyncService:
    def __init__(self, events: list[str], name: str, result=None) -> None:
        self.events = events
        self.name = name
        self.result = result

    async def run_once(self, *, product_id=None):
        self.events.append(self.name)
        return self.result


class FakeSweepService(FakeAsyncService):
    def __init__(self, events: list[str]) -> None:
        super().__init__(
            events,
            "sweep",
            SimpleNamespace(model_dump_json=lambda: '{"status":"ok"}'),
        )
        self.store = MemoryRuntimeStateStore()


def test_worker_reconciles_customer_safety_before_any_autonomous_work(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        worker_module.customer_autopilot_service,
        "reconcile_safety_policy",
        lambda product_id=None: events.append("safety"),
    )
    worker = AutonomousGrowthWorker(
        sweep_service=FakeSweepService(events),
        autoresearch_execution_service=FakeAsyncService(events, "autoresearch-execution"),
        autoresearch_loop_service=FakeAsyncService(events, "autoresearch-loop"),
    )

    assert worker.run(once=True, interval_seconds=60, emit=lambda payload: None) == 0
    assert events == [
        "safety",
        "autoresearch-execution",
        "sweep",
        "autoresearch-loop",
    ]
