from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app import database_advisory_lock
from app.autonomous_controlled_growth import (
    AutonomousControlledGrowthSweepService,
    AutonomousGrowthSweepAlreadyRunning,
)
from app.autonomous_growth_worker import (
    AUTONOMOUS_SWEEP_CONTENTION_RETRY_SECONDS,
    AutonomousGrowthWorker,
)
from app.autonomous_owned_creative_growth import AutonomousOwnedCreativeGrowthSweepService


class DurableStubStore:
    ephemeral = False


class FakeResult:
    def model_dump_json(self) -> str:
        return '{"ok":true}'


class FakeHeartbeat:
    def __init__(self) -> None:
        self.started = 0
        self.running = 0
        self.success: list[int] = []
        self.failed: list[str] = []

    def mark_started(self, worker_name, *, interval_seconds):
        del worker_name, interval_seconds
        self.started += 1

    def mark_running(self, worker_name):
        del worker_name
        self.running += 1

    def mark_success(self, worker_name, *, run_count):
        del worker_name
        self.success.append(run_count)

    def mark_failed(self, worker_name, *, error_type):
        del worker_name
        self.failed.append(error_type)


@pytest.mark.asyncio
async def test_durable_controlled_sweep_refuses_cross_process_contention(monkeypatch) -> None:
    parent_calls = 0

    async def fake_parent_run_once(self, product_id=None):
        nonlocal parent_calls
        del self, product_id
        parent_calls += 1
        return FakeResult()

    monkeypatch.setattr(
        AutonomousOwnedCreativeGrowthSweepService,
        "run_once",
        fake_parent_run_once,
    )

    @contextmanager
    def unavailable_lock():
        yield False

    service = AutonomousControlledGrowthSweepService(
        store=DurableStubStore(),
        sweep_lock_factory=unavailable_lock,
    )

    with pytest.raises(AutonomousGrowthSweepAlreadyRunning, match="another process"):
        await service.run_once()
    assert parent_calls == 0


@pytest.mark.asyncio
async def test_durable_controlled_sweep_holds_and_releases_global_lock(monkeypatch) -> None:
    lifecycle: list[str] = []

    async def fake_parent_run_once(self, product_id=None):
        del self, product_id
        lifecycle.append("sweep")
        return FakeResult()

    monkeypatch.setattr(
        AutonomousOwnedCreativeGrowthSweepService,
        "run_once",
        fake_parent_run_once,
    )

    @contextmanager
    def available_lock():
        lifecycle.append("acquire")
        try:
            yield True
        finally:
            lifecycle.append("release")

    service = AutonomousControlledGrowthSweepService(
        store=DurableStubStore(),
        sweep_lock_factory=available_lock,
    )

    result = await service.run_once()

    assert isinstance(result, FakeResult)
    assert lifecycle == ["acquire", "sweep", "release"]


@pytest.mark.asyncio
async def test_global_lock_releases_when_sweep_raises(monkeypatch) -> None:
    lifecycle: list[str] = []

    async def failing_parent(self, product_id=None):
        del self, product_id
        lifecycle.append("sweep")
        raise ValueError("boom")

    monkeypatch.setattr(
        AutonomousOwnedCreativeGrowthSweepService,
        "run_once",
        failing_parent,
    )

    @contextmanager
    def available_lock():
        lifecycle.append("acquire")
        try:
            yield True
        finally:
            lifecycle.append("release")

    service = AutonomousControlledGrowthSweepService(
        store=DurableStubStore(),
        sweep_lock_factory=available_lock,
    )

    with pytest.raises(ValueError, match="boom"):
        await service.run_once()
    assert lifecycle == ["acquire", "sweep", "release"]


def test_recurring_worker_retries_contention_without_marking_false_success() -> None:
    class SweepService:
        store = DurableStubStore()

        def __init__(self) -> None:
            self.calls = 0

        async def run_once(self, product_id=None):
            del product_id
            self.calls += 1
            if self.calls == 1:
                raise AutonomousGrowthSweepAlreadyRunning("busy")
            return FakeResult()

    service = SweepService()
    heartbeat = FakeHeartbeat()
    sleeps: list[float] = []
    emitted: list[str] = []
    worker = AutonomousGrowthWorker(
        sweep_service=service,
        heartbeat_service=heartbeat,
        sleep=sleeps.append,
    )

    result = worker.run(
        once=False,
        interval_seconds=300,
        max_runs=1,
        emit=emitted.append,
    )

    assert result == 0
    assert service.calls == 2
    assert sleeps == [AUTONOMOUS_SWEEP_CONTENTION_RETRY_SECONDS]
    assert heartbeat.started == 1
    assert heartbeat.running == 2
    assert heartbeat.success == [1]
    assert heartbeat.failed == []
    assert '"status": "skipped"' in emitted[0]
    assert emitted[1] == '{"ok":true}'


class ScalarResult:
    def __init__(self, value: bool) -> None:
        self.value = value

    def scalar_one(self) -> bool:
        return self.value


class FakeConnection:
    def __init__(self, results: list[bool]) -> None:
        self.results = iter(results)
        self.statements: list[str] = []
        self.invalidated = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement, parameters):
        del parameters
        self.statements.append(str(statement))
        return ScalarResult(next(self.results))

    def invalidate(self) -> None:
        self.invalidated = True


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def connect(self) -> FakeConnection:
        return self.connection


def test_postgres_advisory_lock_explicitly_unlocks_before_pool_return(monkeypatch) -> None:
    connection = FakeConnection([True, True])
    monkeypatch.setattr(
        database_advisory_lock,
        "get_sync_engine",
        lambda: FakeEngine(connection),
    )

    with database_advisory_lock.postgres_session_advisory_lock(123) as acquired:
        assert acquired is True

    assert len(connection.statements) == 2
    assert "pg_try_advisory_lock" in connection.statements[0]
    assert "pg_advisory_unlock" in connection.statements[1]
    assert connection.invalidated is False


def test_postgres_advisory_lock_does_not_unlock_when_not_acquired(monkeypatch) -> None:
    connection = FakeConnection([False])
    monkeypatch.setattr(
        database_advisory_lock,
        "get_sync_engine",
        lambda: FakeEngine(connection),
    )

    with database_advisory_lock.postgres_session_advisory_lock(123) as acquired:
        assert acquired is False

    assert len(connection.statements) == 1
    assert "pg_try_advisory_lock" in connection.statements[0]
