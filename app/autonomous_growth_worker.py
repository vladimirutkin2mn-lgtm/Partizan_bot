from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Callable
from uuid import UUID

from app.autonomous_growth import AutonomousGrowthSweepService
from app.autonomous_paid_growth import autonomous_paid_growth_sweep_service


class AutonomousGrowthWorker:
    def __init__(
        self,
        *,
        sweep_service: AutonomousGrowthSweepService | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sweep_service = sweep_service or autonomous_paid_growth_sweep_service
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
        runs = 0
        while True:
            result = asyncio.run(self._sweep_service.run_once(product_id=product_id))
            emit(result.model_dump_json())
            runs += 1
            if once or (max_runs is not None and runs >= max_runs):
                return 0
            self._sleep(float(interval_seconds))


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
