from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel

from app.runtime_store import RuntimeStateStore, get_runtime_store

WORKER_HEARTBEAT_NAMESPACE = "worker_heartbeat"
PAID_CONTROL_WORKER = "paid-control-worker"
AUTONOMOUS_GROWTH_WORKER = "autonomous-growth-worker"
REQUIRED_PRODUCTION_WORKERS = (PAID_CONTROL_WORKER, AUTONOMOUS_GROWTH_WORKER)


class WorkerLifecycleState(StrEnum):
    MISSING = "MISSING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    FAILED = "FAILED"


class WorkerHeartbeatView(BaseModel):
    worker_name: str
    state: WorkerLifecycleState
    healthy: bool
    interval_seconds: int | None = None
    run_count: int = 0
    started_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_type: str | None = None
    heartbeat_age_seconds: float | None = None
    stale_after_seconds: float | None = None


class WorkerHealthView(BaseModel):
    healthy: bool
    workers: list[WorkerHeartbeatView]


class WorkerHeartbeatService:
    def __init__(
        self,
        store: RuntimeStateStore | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store or get_runtime_store()
        self._now = now or (lambda: datetime.now(UTC))

    def mark_started(self, worker_name: str, *, interval_seconds: int) -> None:
        now = self._now()
        self._put(
            worker_name,
            state=WorkerLifecycleState.STARTING,
            interval_seconds=interval_seconds,
            run_count=0,
            started_at=now,
            last_heartbeat_at=now,
            last_success_at=None,
            last_error_type=None,
        )

    def mark_running(self, worker_name: str) -> None:
        current = self._require_current(worker_name)
        current["state"] = WorkerLifecycleState.RUNNING.value
        current["last_heartbeat_at"] = self._now().isoformat()
        current["last_error_type"] = None
        self.store.put(WORKER_HEARTBEAT_NAMESPACE, worker_name, current)

    def mark_success(self, worker_name: str, *, run_count: int) -> None:
        current = self._require_current(worker_name)
        now = self._now()
        current.update(
            {
                "state": WorkerLifecycleState.IDLE.value,
                "run_count": run_count,
                "last_heartbeat_at": now.isoformat(),
                "last_success_at": now.isoformat(),
                "last_error_type": None,
            }
        )
        self.store.put(WORKER_HEARTBEAT_NAMESPACE, worker_name, current)

    def mark_failed(self, worker_name: str, *, error_type: str) -> None:
        current = self._require_current(worker_name)
        current.update(
            {
                "state": WorkerLifecycleState.FAILED.value,
                "last_heartbeat_at": self._now().isoformat(),
                "last_error_type": error_type[:120],
            }
        )
        self.store.put(WORKER_HEARTBEAT_NAMESPACE, worker_name, current)

    def health(self) -> WorkerHealthView:
        now = self._now()
        workers = [self._view(worker_name, now) for worker_name in REQUIRED_PRODUCTION_WORKERS]
        return WorkerHealthView(
            healthy=all(worker.healthy for worker in workers),
            workers=workers,
        )

    def _view(self, worker_name: str, now: datetime) -> WorkerHeartbeatView:
        payload = self.store.get(WORKER_HEARTBEAT_NAMESPACE, worker_name)
        if payload is None:
            return WorkerHeartbeatView(
                worker_name=worker_name,
                state=WorkerLifecycleState.MISSING,
                healthy=False,
            )

        interval_seconds = int(payload["interval_seconds"])
        stale_after = float(max(60, interval_seconds * 2 + 30))
        heartbeat_at = self._parse_datetime(payload.get("last_heartbeat_at"))
        success_at = self._parse_datetime(payload.get("last_success_at"))
        age = None if heartbeat_at is None else max(0.0, (now - heartbeat_at).total_seconds())
        state = WorkerLifecycleState(str(payload["state"]))
        healthy = (
            state != WorkerLifecycleState.FAILED
            and success_at is not None
            and heartbeat_at is not None
            and age is not None
            and age <= stale_after
        )
        return WorkerHeartbeatView(
            worker_name=worker_name,
            state=state,
            healthy=healthy,
            interval_seconds=interval_seconds,
            run_count=int(payload.get("run_count", 0)),
            started_at=self._parse_datetime(payload.get("started_at")),
            last_heartbeat_at=heartbeat_at,
            last_success_at=success_at,
            last_error_type=payload.get("last_error_type"),
            heartbeat_age_seconds=age,
            stale_after_seconds=stale_after,
        )

    def _require_current(self, worker_name: str) -> dict:
        payload = self.store.get(WORKER_HEARTBEAT_NAMESPACE, worker_name)
        if payload is None:
            raise RuntimeError(f"Worker heartbeat not started: {worker_name}")
        return payload

    def _put(
        self,
        worker_name: str,
        *,
        state: WorkerLifecycleState,
        interval_seconds: int,
        run_count: int,
        started_at: datetime,
        last_heartbeat_at: datetime,
        last_success_at: datetime | None,
        last_error_type: str | None,
    ) -> None:
        self.store.put(
            WORKER_HEARTBEAT_NAMESPACE,
            worker_name,
            {
                "worker_name": worker_name,
                "state": state.value,
                "interval_seconds": interval_seconds,
                "run_count": run_count,
                "started_at": started_at.isoformat(),
                "last_heartbeat_at": last_heartbeat_at.isoformat(),
                "last_success_at": (
                    last_success_at.isoformat() if last_success_at is not None else None
                ),
                "last_error_type": last_error_type,
            },
        )

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


worker_heartbeat_service = WorkerHeartbeatService()
