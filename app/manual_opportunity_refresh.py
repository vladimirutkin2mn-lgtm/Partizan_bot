from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from app.autonomous_opportunity_refresh import (
    AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE,
    DEFAULT_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS,
    AutonomousOpportunityRefreshService,
    autonomous_opportunity_refresh_service,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store


class ManualOpportunityRefreshService:
    """Run opportunity discovery on demand without entering an execution sweep.

    `force=True` bypasses only the discovery cadence marker for one product. The
    underlying AutonomousOpportunityRefreshService remains responsible for research,
    Telegram preflight and portfolio rebuilding. It does not publish, join a community,
    change a profile or execute a Distribution Play.
    """

    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        refresh_service: AutonomousOpportunityRefreshService | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._refresh_service = refresh_service or autonomous_opportunity_refresh_service

    async def run_once(
        self,
        *,
        product_id: UUID,
        force: bool = False,
    ) -> list[dict]:
        if force:
            key = str(product_id)
            previous = self._store.get(AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE, key)
            if previous is not None:
                forced = dict(previous)
                forced["last_success_at"] = None
                forced["manual_force_requested_at"] = datetime.now(UTC).isoformat()
                self._store.put(AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE, key, forced)

        return await self._refresh_service.run_once(
            product_id=product_id,
            interval_seconds=DEFAULT_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS,
        )


manual_opportunity_refresh_service = ManualOpportunityRefreshService()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh Partizan public acquisition opportunities for one product without "
            "running autonomous execution."
        )
    )
    parser.add_argument(
        "--product-id",
        type=UUID,
        required=True,
        help="Product whose opportunity portfolio should be refreshed.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the normal discovery cadence for this one refresh.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        results = asyncio.run(
            manual_opportunity_refresh_service.run_once(
                product_id=args.product_id,
                force=args.force,
            )
        )
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}))
        return 2

    print(json.dumps({"results": results}, ensure_ascii=False))
    if not results:
        return 2
    if any(row.get("status") == "FAILED" for row in results):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
