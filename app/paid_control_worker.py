from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable

from app.paid_control_sweep import PaidControlSweepService, paid_control_sweep_service


class PaidControlWorker:
    def __init__(
        self,
        *,
        sweep_service: PaidControlSweepService | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sweep_service = sweep_service or paid_control_sweep_service
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
        runs = 0
        while True:
            result = self._sweep_service.run_once()
            emit(result.model_dump_json())
            runs += 1
            if once or (max_runs is not None and runs >= max_runs):
                return 0
            self._sleep(float(interval_seconds))


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
