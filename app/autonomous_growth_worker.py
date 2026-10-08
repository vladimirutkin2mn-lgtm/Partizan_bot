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
from app.autonomous_opportunity_refresh import (
    DEFAULT_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS,
    AutonomousOpportunityRefreshService,
    autonomous_opportunity_refresh_service,
)
from app.customer_autopilot import customer_autopilot_service
from app.growth_autoresearch_execution import GrowthAutoResearchExecutionService
from app.growth_autoresearch_execution_runtime import (
    growth_autoresearch_execution_runtime_service,
)
from app.growth_autoresearch_loop import (
    GrowthAutoResearchLoopService,
    growth_autoresearch_loop_service,
)
from app.worker_health import AUTONOMOUS_GROWTH_WORKER, WorkerHeartbeatService

AUTONOMOUS_SWEEP_CONTENTION_RETRY_SECONDS = 5.0


class AutonomousGrowthWorker:
    def __init__(
        self,
        *,
        sweep_service: AutonomousGrowthSweepService | None = None,
        autoresearch_execution_service: GrowthAutoResearchExecutionService | None = None,
        autoresearch_loop_service: GrowthAutoResearchLoopService | None = None,
        opportunity_refresh_service: AutonomousOpportunityRefreshService | None = None,
        heartbeat_service: WorkerHeartbeatService | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sweep_service = sweep_service or autonomous_controlled_growth_sweep_service
        self._autoresearch_execution_service = (
            autoresearch_execution_service or growth_autoresearch_execution_runtime_service
        )
        self._autoresearch_loop_service = (
            autoresearch_loop_service or growth_autoresearch_loop_service
        )
        self._opportunity_refresh_service = (
            opportunity_refresh_service or autonomous_opportunity_refresh_service
        )
        self._heartbeat_service = heartbeat_service
        self._sleep = sleep

    def run(
        self,
        *,
        once: bool,
        interval_seconds: int,
        discovery_interval_seconds: int = DEFAULT_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS,
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
                # Reconcile customer Autopilot safety before any autonomous provider work.
                # CHANNELS/FUNDING/SETUP pauses are provider-confirmed and never auto-resumed.
                customer_autopilot_service.reconcile_safety_policy(product_id=product_id)

                # Fresh public opportunities are a separate research concern from the
                # five-minute execution loop. Refresh them on a slower cadence, rebuild
                # Distribution Plays, then let the existing mandate/governance stack decide
                # whether any concrete action is allowed. This step never publishes or joins.
                asyncio.run(
                    self._opportunity_refresh_service.run_once(
                        product_id=product_id,
                        interval_seconds=discovery_interval_seconds,
                    )
                )

                # Give an existing READY AutoResearch challenger first access to its exact,
                # permissioned non-paid DistributionPlay. The bridge still delegates every
                # mutation to the existing mandate, drafting, approval and adapter control plane.
                asyncio.run(
                    self._autoresearch_execution_service.run_once(product_id=product_id)
                )
                result = asyncio.run(self._sweep_service.run_once(product_id=product_id))
                asyncio.run(self._autoresearch_loop_service.run_once(product_id=product_id))
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
        "--discovery-interval-seconds",
        type=int,
        default=DEFAULT_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS,
        help=(
            "Seconds between fresh public opportunity discovery refreshes "
            "for active Telegram Auto products (minimum 900)."
        ),
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
            discovery_interval_seconds=args.discovery_interval_seconds,
            product_id=args.product_id,
        )
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
