from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.autonomous_growth_worker import AutonomousGrowthWorker
from app.config import Settings, get_settings
from app.main import app
from app.operator_auth import OPERATOR_KEY_HEADER
from app.paid_control_worker import PaidControlWorker
from app.runtime_store import MemoryRuntimeStateStore, get_runtime_store
from app.worker_health import (
    AUTONOMOUS_GROWTH_WORKER,
    PAID_CONTROL_WORKER,
    WORKER_HEARTBEAT_NAMESPACE,
    WorkerHeartbeatService,
    WorkerLifecycleState,
)

client = TestClient(app)


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class SweepResult:
    def model_dump_json(self) -> str:
        return "{}"


class PaidSweep:
    def __init__(self, store, *, error: Exception | None = None) -> None:
        self.store = store
        self.error = error

    def run_once(self):
        if self.error is not None:
            raise self.error
        return SweepResult()


class AutonomousSweep:
    def __init__(self, store, *, error: Exception | None = None) -> None:
        self.store = store
        self.error = error

    async def run_once(self, product_id=None):
        if self.error is not None:
            raise self.error
        return SweepResult()


@pytest.fixture(autouse=True)
def reset_worker_health_state() -> None:
    app.dependency_overrides.pop(get_settings, None)
    store = get_runtime_store()
    if store.ephemeral:
        store.clear_namespace(WORKER_HEARTBEAT_NAMESPACE)
    yield
    app.dependency_overrides.pop(get_settings, None)
    if store.ephemeral:
        store.clear_namespace(WORKER_HEARTBEAT_NAMESPACE)


def test_worker_health_requires_success_from_both_workers() -> None:
    store = MemoryRuntimeStateStore()
    heartbeat = WorkerHeartbeatService(store)

    missing = heartbeat.health()
    heartbeat.mark_started(PAID_CONTROL_WORKER, interval_seconds=60)
    heartbeat.mark_started(AUTONOMOUS_GROWTH_WORKER, interval_seconds=300)
    starting = heartbeat.health()
    heartbeat.mark_success(PAID_CONTROL_WORKER, run_count=1)
    one_ready = heartbeat.health()
    heartbeat.mark_success(AUTONOMOUS_GROWTH_WORKER, run_count=1)
    ready = heartbeat.health()

    assert missing.healthy is False
    assert all(item.state == WorkerLifecycleState.MISSING for item in missing.workers)
    assert starting.healthy is False
    assert one_ready.healthy is False
    assert ready.healthy is True
    assert all(item.healthy for item in ready.workers)


def test_restart_clears_prior_success_until_new_sweep() -> None:
    store = MemoryRuntimeStateStore()
    heartbeat = WorkerHeartbeatService(store)
    heartbeat.mark_started(PAID_CONTROL_WORKER, interval_seconds=60)
    heartbeat.mark_success(PAID_CONTROL_WORKER, run_count=4)

    before_restart = heartbeat.health().workers[0]
    heartbeat.mark_started(PAID_CONTROL_WORKER, interval_seconds=60)
    after_restart = heartbeat.health().workers[0]
    heartbeat.mark_success(PAID_CONTROL_WORKER, run_count=1)
    after_new_sweep = heartbeat.health().workers[0]

    assert before_restart.healthy is True
    assert before_restart.run_count == 4
    assert after_restart.healthy is False
    assert after_restart.state == WorkerLifecycleState.STARTING
    assert after_restart.run_count == 0
    assert after_restart.last_success_at is None
    assert after_new_sweep.healthy is True
    assert after_new_sweep.run_count == 1


def test_worker_health_becomes_stale_relative_to_interval() -> None:
    store = MemoryRuntimeStateStore()
    clock = MutableClock()
    heartbeat = WorkerHeartbeatService(store, now=clock.now)
    heartbeat.mark_started(PAID_CONTROL_WORKER, interval_seconds=60)
    heartbeat.mark_success(PAID_CONTROL_WORKER, run_count=1)
    heartbeat.mark_started(AUTONOMOUS_GROWTH_WORKER, interval_seconds=300)
    heartbeat.mark_success(AUTONOMOUS_GROWTH_WORKER, run_count=1)

    assert heartbeat.health().healthy is True
    clock.advance(seconds=151)
    state = heartbeat.health()

    paid = next(item for item in state.workers if item.worker_name == PAID_CONTROL_WORKER)
    autonomous = next(
        item for item in state.workers if item.worker_name == AUTONOMOUS_GROWTH_WORKER
    )
    assert state.healthy is False
    assert paid.healthy is False
    assert paid.stale_after_seconds == 150
    assert autonomous.healthy is True


def test_paid_worker_writes_successful_heartbeat() -> None:
    store = MemoryRuntimeStateStore()
    worker = PaidControlWorker(sweep_service=PaidSweep(store))

    result = worker.run(
        once=True,
        interval_seconds=60,
        emit=lambda _: None,
    )

    heartbeat = WorkerHeartbeatService(store).health().workers[0]
    assert result == 0
    assert heartbeat.worker_name == PAID_CONTROL_WORKER
    assert heartbeat.healthy is True
    assert heartbeat.run_count == 1
    assert heartbeat.state == WorkerLifecycleState.IDLE


def test_autonomous_worker_writes_successful_heartbeat() -> None:
    store = MemoryRuntimeStateStore()
    worker = AutonomousGrowthWorker(sweep_service=AutonomousSweep(store))

    result = worker.run(
        once=True,
        interval_seconds=300,
        emit=lambda _: None,
    )

    heartbeat = WorkerHeartbeatService(store).health().workers[1]
    assert result == 0
    assert heartbeat.worker_name == AUTONOMOUS_GROWTH_WORKER
    assert heartbeat.healthy is True
    assert heartbeat.run_count == 1
    assert heartbeat.state == WorkerLifecycleState.IDLE


def test_worker_failure_records_only_exception_type() -> None:
    store = MemoryRuntimeStateStore()
    secret_message = "provider failed with token super-secret-value"
    worker = PaidControlWorker(
        sweep_service=PaidSweep(store, error=RuntimeError(secret_message)),
    )

    with pytest.raises(RuntimeError, match="super-secret-value"):
        worker.run(once=True, interval_seconds=60, emit=lambda _: None)

    heartbeat = WorkerHeartbeatService(store).health().workers[0]
    persisted = store.get(WORKER_HEARTBEAT_NAMESPACE, PAID_CONTROL_WORKER)
    assert heartbeat.state == WorkerLifecycleState.FAILED
    assert heartbeat.healthy is False
    assert heartbeat.last_error_type == "RuntimeError"
    assert secret_message not in str(persisted)
    assert "super-secret-value" not in str(persisted)


def test_worker_health_endpoint_is_operator_authenticated_in_production() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        app_env="production",
        operator_api_key="correct-secret",
    )

    blocked = client.get("/v1/ops/workers/health")
    allowed = client.get(
        "/v1/ops/workers/health",
        headers={OPERATOR_KEY_HEADER: "correct-secret"},
    )

    assert blocked.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["healthy"] is False
    assert {item["worker_name"] for item in allowed.json()["workers"]} == {
        PAID_CONTROL_WORKER,
        AUTONOMOUS_GROWTH_WORKER,
    }
