from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable

from app.paid_control_sweep import PaidControlSweepService, paid_control_sweep_service
from app.worker_health import PAID_CONTROL_WORKER, WorkerHeartbeatService


class PaidControlWorker:
    def __init__(
        self,
        *,
        sweep_service: PaidControlSweepService | None = None,
        heartbeat_service: WorkerHeartbeatService | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sweep_service = sweep_service or paid_control_sweep_service
        self._heartbeat_service = heartbeat_service
        self._sleep = sleep

    def run(
        self,
        *,
        once: bool,
        interval_seconds: int,
        max_runs: int | None = None,
        emit: Callable[[str], None] = print,
    ) -> int:
        if interval_seconds < 15:
            raise ValueError("paid-control interval_seconds must be at least 15")
        if not once and self._sweep_service.store.ephemeral:
            raise RuntimeError(
                "Recurring paid-control worker requires RUNTIME_STORAGE=database"
            )
        heartbeat = self._heartbeat()
        if heartbeat is not None:
            heartbeat.mark_started(PAID_CONTROL_WORKER, interval_seconds=interval_seconds)
        runs = 0
        while True:
            if heartbeat is not None:
                heartbeat.mark_running(PAID_CONTROL_WORKER)
            try:
                result = self._sweep_service.run_once()
            except Exception as exc:
                if heartbeat is not None:
                    heartbeat.mark_failed(
                        PAID_CONTROL_WORKER,
                        error_type=type(exc).__name__,
                    )
                raise
            emit(result.model_dump_json())
            runs += 1
            if heartbeat is not None:
                heartbeat.mark_success(PAID_CONTROL_WORKER, run_count=runs)
            if once or (max_runs is not None and runs >= max_runs):
                return 0
            self._sleep(float(interval_seconds))

    def _heartbeat(self) -> WorkerHeartbeatService | None:
        if self._heartbeat_service is not None:
            return self._heartbeat_service
        store = getattr(self._sweep_service, "store", None)
        return WorkerHeartbeatService(store) if store is not None else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize and enforce Partizan paid-provider control guardrails."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one sweep and exit. Recurring mode is the default.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=60,
        help="Seconds between recurring provider-control sweeps (minimum 15).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    worker = PaidControlWorker()
    try:
        return worker.run(
            once=args.once,
            interval_seconds=args.interval_seconds,
        )
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
