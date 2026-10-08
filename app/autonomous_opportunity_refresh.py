from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.audience_intelligence_service import (
    InMemoryAudienceIntelligenceService,
    audience_intelligence_service,
)
from app.autonomy_schemas import GrowthMandateStatus, GrowthMandateView
from app.autonomy_service import GROWTH_MANDATE_NAMESPACE, growth_mandate_service
from app.customer_live_opportunities import (
    CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE,
    CUSTOMER_PROJECT_NAMESPACE,
    customer_live_opportunity_service,
)
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_play_service import distribution_play_service
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.icp_service import icp_service
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_opportunity_preflight import (
    TelegramOpportunityPreflightResult,
    TelegramOpportunityPreflightService,
    telegram_opportunity_preflight_service,
)

AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE = "autonomous_opportunity_refresh"
DEFAULT_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS = 6 * 60 * 60
MIN_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS = 15 * 60


class AutonomousOpportunityRefreshService:
    """Periodically refresh execution opportunities for active autonomous products.

    Discovery is deliberately separate from execution. This service may read public
    evidence, persist fresh opportunities and rebuild Distribution Plays, but it never
    publishes, joins a community, changes a profile or mutates a provider account.

    Exact Telegram comment targets are additionally checked with the connected customer
    account before they can become autonomous execution candidates. The preflight is
    read-only: no linked group is joined automatically.
    """

    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        audience_service: InMemoryAudienceIntelligenceService | None = None,
        telegram_preflight_service: TelegramOpportunityPreflightService | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._audience_service = audience_service or audience_intelligence_service
        self._telegram_preflight = (
            telegram_preflight_service or telegram_opportunity_preflight_service
        )

    async def run_once(
        self,
        *,
        product_id: UUID | None = None,
        interval_seconds: int = DEFAULT_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS,
    ) -> list[dict]:
        if interval_seconds < MIN_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS:
            raise ValueError(
                "autonomous opportunity refresh interval must be at least 900 seconds"
            )

        now = datetime.now(UTC)
        results: list[dict] = []
        for candidate in self._candidate_mandates(product_id):
            previous = self._store.get(
                AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE,
                str(candidate.product_id),
            )
            if not self._due(previous, now=now, interval_seconds=interval_seconds):
                results.append(
                    {
                        "product_id": str(candidate.product_id),
                        "status": "NOT_DUE",
                        "last_success_at": previous.get("last_success_at") if previous else None,
                    }
                )
                continue

            started_at = datetime.now(UTC)
            try:
                product = product_intake_service.get_product(candidate.product_id)
                icps = icp_service.get(candidate.product_id)
                distribution = await self._audience_service.discover(product, icps)

                project_id = self._project_id_for_product(candidate.product_id)
                preflight_by_url: dict[str, TelegramOpportunityPreflightResult] = {}
                if project_id is not None:
                    preflight_by_url = await self._preflight_live_comment_targets(project_id)
                    distribution = await self._apply_comment_preflight_to_distribution(
                        project_id,
                        distribution,
                        preflight_by_url,
                    )

                plays = distribution_play_service.generate(
                    product,
                    distribution,
                    identities=distribution_control_plane_service.list_identities(),
                    community_policies=distribution_control_plane_service.list_policies(),
                    campaign_slots=distribution_control_plane_service.list_campaign_slots(),
                )
            except Exception as exc:
                # Discovery failures must not disable the existing portfolio. Persist a
                # safe diagnostic and allow the normal autonomous sweep to continue using
                # the last known-good plays.
                record = {
                    "product_id": str(candidate.product_id),
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                    "attempted_at": started_at.isoformat(),
                    "last_success_at": previous.get("last_success_at") if previous else None,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
                self._store.put(
                    AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE,
                    str(candidate.product_id),
                    record,
                )
                results.append(record)
                continue

            completed_at = datetime.now(UTC)
            statuses = [item.status for item in preflight_by_url.values()]
            record = {
                "product_id": str(candidate.product_id),
                "status": "REFRESHED",
                "opportunity_count": distribution.opportunity_count,
                "play_count": plays.play_count,
                "ready_play_count": plays.ready_count,
                "telegram_preflight_checked": len(statuses),
                "telegram_preflight_ready": statuses.count("READY"),
                "telegram_preflight_join_required": statuses.count("JOIN_REQUIRED"),
                "telegram_preflight_excluded": sum(
                    status not in {"READY", "JOIN_REQUIRED"} for status in statuses
                ),
                "attempted_at": started_at.isoformat(),
                "last_success_at": completed_at.isoformat(),
                "updated_at": completed_at.isoformat(),
            }
            self._store.put(
                AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE,
                str(candidate.product_id),
                record,
            )
            results.append(record)
        return results

    async def _preflight_live_comment_targets(
        self,
        project_id: UUID,
    ) -> dict[str, TelegramOpportunityPreflightResult]:
        results: dict[str, TelegramOpportunityPreflightResult] = {}
        for opportunity in customer_live_opportunity_service.list_for_project(project_id):
            if opportunity.status != "ACTIVE" or opportunity.freshness == "STALE":
                continue
            if opportunity.platform != "TELEGRAM":
                continue
            if opportunity.kind not in {"COMMENT", "CHANNEL"}:
                continue
            target_url = str(opportunity.url)
            normalized = self._normalized_url(target_url)
            if normalized in results:
                continue
            result = await self._telegram_preflight.check_comment_target(
                project_id,
                target_url,
            )
            results[normalized] = result
            self._persist_live_preflight(project_id, target_url, result)
        return results

    async def _apply_comment_preflight_to_distribution(
        self,
        project_id: UUID,
        distribution,
        preflight_by_url: dict[str, TelegramOpportunityPreflightResult],
    ):
        opportunities = list(getattr(distribution, "opportunities", []) or [])
        if not opportunities:
            return distribution

        updated_opportunities = []
        for opportunity in opportunities:
            if (
                getattr(opportunity, "platform", None) != DistributionPlatform.TELEGRAM
                or getattr(opportunity, "kind", None) != OpportunityKind.CHANNEL
            ):
                updated_opportunities.append(opportunity)
                continue

            metadata = (
                dict(opportunity.metadata)
                if isinstance(getattr(opportunity, "metadata", None), dict)
                else {}
            )
            target_url = str(metadata.get("action_target_url") or "").strip()
            if not target_url or metadata.get("action_target_specific") is not True:
                updated_opportunities.append(opportunity)
                continue

            capabilities = (
                dict(metadata.get("surface_capabilities"))
                if isinstance(metadata.get("surface_capabilities"), dict)
                else {}
            )
            native_comment_surface = str(capabilities.get("comment") or "").upper()
            metadata["native_comment_surface"] = native_comment_surface or "UNKNOWN"

            normalized = self._normalized_url(target_url)
            result = preflight_by_url.get(normalized)
            if result is None:
                if native_comment_surface != "AVAILABLE":
                    result = TelegramOpportunityPreflightResult(
                        status="NO_DISCUSSION",
                        handle=str(metadata.get("handle") or "") or None,
                        reason="NATIVE_COMMENT_SURFACE_UNAVAILABLE",
                    )
                else:
                    result = await self._telegram_preflight.check_comment_target(
                        project_id,
                        target_url,
                    )
                    preflight_by_url[normalized] = result
                    self._persist_live_preflight(project_id, target_url, result)

            metadata["publisher_comment_preflight"] = {
                **result.public_dict(),
                "checked_at": datetime.now(UTC).isoformat(),
            }
            # Existing DistributionPlayPlanner already blocks client-owned COMMENT
            # execution when comment capability is not AVAILABLE. Reuse that boundary:
            # only a preflight-confirmed READY target remains executable. JOIN_REQUIRED
            # stays visible to the customer but cannot be selected until membership is
            # handled through a separately authorized operation.
            capabilities["comment"] = "AVAILABLE" if result.execution_ready else "UNAVAILABLE"
            metadata["surface_capabilities"] = capabilities

            updated = opportunity.model_copy(update={"metadata": metadata})
            updated_opportunities.append(updated)
            update_opportunity = getattr(self._audience_service, "update_opportunity", None)
            if callable(update_opportunity):
                update_opportunity(updated)

        if hasattr(distribution, "model_copy"):
            return distribution.model_copy(update={"opportunities": updated_opportunities})
        return distribution

    def _persist_live_preflight(
        self,
        project_id: UUID,
        target_url: str,
        result: TelegramOpportunityPreflightResult,
    ) -> None:
        normalized = self._normalized_url(target_url)
        publishability = (
            result.status
            if result.status in {
                "READY",
                "JOIN_REQUIRED",
                "NO_WRITE_ACCESS",
                "NO_DISCUSSION",
                "PRECHECK_FAILED",
            }
            else "PRECHECK_FAILED"
        )
        status = "ACTIVE" if result.recoverable else "STALE"
        now = datetime.now(UTC).isoformat()
        detail = f"Telegram publisher preflight: {result.status}"
        if result.reason:
            detail = f"{detail} ({result.reason})"

        for row in self._store.list_namespace(CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE):
            if str(row.get("project_id") or "") != str(project_id):
                continue
            if self._normalized_url(str(row.get("url") or "")) != normalized:
                continue
            if str(row.get("status") or "") == "PUBLISHED":
                continue
            row["publishability"] = publishability
            row["publishability_detail"] = detail[:1200]
            row["last_checked_at"] = now
            row["status"] = status
            row["updated_at"] = now
            key = f"{project_id}:{row['opportunity_id']}"
            self._store.put(CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE, key, row)

    def _project_id_for_product(self, product_id: UUID) -> UUID | None:
        matches: list[UUID] = []
        for project in self._store.list_namespace(CUSTOMER_PROJECT_NAMESPACE):
            if project.get("deleted_at"):
                continue
            if str(project.get("product_id") or "") != str(product_id):
                continue
            try:
                matches.append(UUID(str(project["id"])))
            except (KeyError, TypeError, ValueError):
                continue
        return matches[0] if len(matches) == 1 else None

    def _candidate_mandates(self, product_id: UUID | None) -> list[GrowthMandateView]:
        if product_id is not None:
            try:
                mandate = growth_mandate_service.get(product_id)
            except KeyError:
                return []
            return [mandate] if self._eligible(mandate) else []

        mandates: list[GrowthMandateView] = []
        for payload in self._store.list_namespace(GROWTH_MANDATE_NAMESPACE):
            try:
                mandate = GrowthMandateView.model_validate(payload)
            except ValueError:
                continue
            if self._eligible(mandate):
                mandates.append(mandate)
        mandates.sort(key=lambda item: str(item.product_id))
        return mandates

    @staticmethod
    def _eligible(mandate: GrowthMandateView) -> bool:
        return (
            mandate.status == GrowthMandateStatus.ACTIVE
            and DistributionPlatform.TELEGRAM in mandate.allowed_platforms
        )

    @staticmethod
    def _due(previous: dict | None, *, now: datetime, interval_seconds: int) -> bool:
        if not previous:
            return True
        raw = previous.get("last_success_at")
        if not raw:
            return True
        try:
            last_success = datetime.fromisoformat(str(raw))
        except ValueError:
            return True
        if last_success.tzinfo is None:
            last_success = last_success.replace(tzinfo=UTC)
        else:
            last_success = last_success.astimezone(UTC)
        return now >= last_success + timedelta(seconds=interval_seconds)

    @staticmethod
    def _normalized_url(value: str) -> str:
        return str(value or "").strip().rstrip("/")

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE)


autonomous_opportunity_refresh_service = AutonomousOpportunityRefreshService()
