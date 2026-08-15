from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Callable
from uuid import UUID

from app.autonomous_controlled_growth import (
    AutonomousGrowthSweepAlreadyRunning,
    autonomous_controlled_growth_sweep_service,
)
from app.autonomous_growth import AutonomousGrowthSweepService
from app.worker_health import AUTONOMOUS_GROWTH_WORKER, WorkerHeartbeatService

AUTONOMOUS_SWEEP_CONTENTION_RETRY_SECONDS = 5.0


class AutonomousGrowthWorker:
    def __init__(
        self,
        *,
        sweep_service: AutonomousGrowthSweepService | None = None,
        heartbeat_service: WorkerHeartbeatService | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sweep_service = sweep_service or autonomous_controlled_growth_sweep_service
        self._heartbeat_service = heartbeat_service
        self._sleep = sleep

    def run(
        self,
        *,
        once: bool,
        interval_seconds: int,
        product_id: UUID | None = None,
        max_runs: int | None = None,
        emit: Callable[[str], None] = print,
    ) -> int:
        if interval_seconds < 60:
            raise ValueError("autonomous-growth interval_seconds must be at least 60")
        if not once and self._sweep_service.store.ephemeral:
            raise RuntimeError(
                "Recurring autonomous-growth worker requires RUNTIME_STORAGE=database"
            )
        heartbeat = self._heartbeat()
        if heartbeat is not None:
            heartbeat.mark_started(AUTONOMOUS_GROWTH_WORKER, interval_seconds=interval_seconds)
        runs = 0
        while True:
            if heartbeat is not None:
                heartbeat.mark_running(AUTONOMOUS_GROWTH_WORKER)
            try:
                result = asyncio.run(self._sweep_service.run_once(product_id=product_id))
            except AutonomousGrowthSweepAlreadyRunning as exc:
                emit(json.dumps({"status": "skipped", "reason": str(exc)}))
                if once:
                    return 0
                self._sleep(AUTONOMOUS_SWEEP_CONTENTION_RETRY_SECONDS)
                continue
            except Exception as exc:
                if heartbeat is not None:
                    heartbeat.mark_failed(
                        AUTONOMOUS_GROWTH_WORKER,
                        error_type=type(exc).__name__,
                    )
                raise
            emit(result.model_dump_json())
            runs += 1
            if heartbeat is not None:
                heartbeat.mark_success(AUTONOMOUS_GROWTH_WORKER, run_count=runs)
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
        description=(
            "Run bounded Partizan autonomous growth actions inside active Growth Mandates."
        )
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one sweep and exit. Recurring mode is the default.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=300,
        help="Seconds between autonomous growth sweeps (minimum 60).",
    )
    parser.add_argument(
        "--product-id",
        type=UUID,
        default=None,
        help="Optionally restrict the sweep to one product Growth Mandate.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    worker = AutonomousGrowthWorker()
    try:
        return worker.run(
            once=args.once,
            interval_seconds=args.interval_seconds,
            product_id=args.product_id,
        )
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
