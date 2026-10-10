from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from app.audience_intelligence_service import audience_intelligence_service
from app.autonomous_opportunity_refresh import (
    AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE,
    DEFAULT_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS,
    AutonomousOpportunityRefreshService,
    autonomous_opportunity_refresh_service,
)
from app.customer_live_opportunities import (
    CUSTOMER_PROJECT_NAMESPACE,
    customer_live_opportunity_service,
)
from app.icp_service import icp_service
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_opportunity_preflight import telegram_opportunity_preflight_service


class ManualOpportunityRefreshService:
    """Run opportunity discovery on demand without entering an execution sweep.

    `force=True` bypasses only the discovery cadence marker for one product. The
    normal path delegates to AutonomousOpportunityRefreshService so active products
    retain the same adaptive discovery behavior. If that delegate returns no rows
    because the product has no eligible active growth mandate, the manual operation
    falls back to an explicit read-only Audience Intelligence pass for the requested
    product. That fallback persists fresh research targets and performs Telegram
    preflight, but it never enables a mandate, publishes, joins a community, changes
    a profile or executes a Distribution Play.
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

        results = await self._refresh_service.run_once(
            product_id=product_id,
            interval_seconds=DEFAULT_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS,
        )
        if results:
            return results

        # A manually requested product refresh is research-only and should not silently
        # become a no-op merely because autonomous execution is paused or absent.
        return [await self._run_direct_product_discovery(product_id)]

    async def _run_direct_product_discovery(self, product_id: UUID) -> dict:
        product = product_intake_service.get_product(product_id)
        icps = icp_service.get(product_id)
        distribution = await audience_intelligence_service.discover(product, icps)

        statuses: list[str] = []
        checked: set[tuple[UUID, str]] = set()
        for project_id in self._project_ids_for_product(product_id):
            for opportunity in customer_live_opportunity_service.list_for_project(project_id):
                if opportunity.status != "ACTIVE" or opportunity.freshness == "STALE":
                    continue
                if opportunity.platform != "TELEGRAM":
                    continue
                if opportunity.kind not in {"COMMENT", "CHANNEL"}:
                    continue

                target_url = str(opportunity.url).strip().rstrip("/")
                key = (project_id, target_url)
                if not target_url or key in checked:
                    continue
                checked.add(key)

                result = await telegram_opportunity_preflight_service.check_comment_target(
                    project_id,
                    target_url,
                )
                status = (
                    result.status
                    if result.status
                    in {
                        "READY",
                        "JOIN_REQUIRED",
                        "NO_WRITE_ACCESS",
                        "NO_DISCUSSION",
                        "PRECHECK_FAILED",
                    }
                    else "PRECHECK_FAILED"
                )
                detail = f"Telegram publisher preflight: {result.status}"
                if result.reason:
                    detail = f"{detail} ({result.reason})"
                customer_live_opportunity_service.update_publishability_by_url(
                    project_id,
                    target_url,
                    publishability=status,
                    detail=detail[:1200],
                )
                statuses.append(status)

        return {
            "product_id": str(product_id),
            "status": "REFRESHED",
            "mode": "MANUAL_DIRECT",
            "opportunity_count": distribution.opportunity_count,
            "telegram_preflight_checked": len(statuses),
            "telegram_preflight_ready": statuses.count("READY"),
            "telegram_preflight_join_required": statuses.count("JOIN_REQUIRED"),
            "telegram_preflight_no_write_access": statuses.count("NO_WRITE_ACCESS"),
            "telegram_preflight_no_discussion": statuses.count("NO_DISCUSSION"),
            "telegram_preflight_failed": statuses.count("PRECHECK_FAILED"),
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def _project_ids_for_product(self, product_id: UUID) -> list[UUID]:
        result: list[UUID] = []
        for project in self._store.list_namespace(CUSTOMER_PROJECT_NAMESPACE):
            if project.get("deleted_at"):
                continue
            if str(project.get("product_id") or "") != str(product_id):
                continue
            try:
                result.append(UUID(str(project["id"])))
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(set(result), key=str)


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
