from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.audience_intelligence_service import (
    InMemoryAudienceIntelligenceService,
    audience_intelligence_service,
)
from app.autonomy_schemas import GrowthMandateStatus, GrowthMandateView
from app.autonomy_service import GROWTH_MANDATE_NAMESPACE, growth_mandate_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_play_service import distribution_play_service
from app.distribution_types import DistributionPlatform
from app.icp_service import icp_service
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE = "autonomous_opportunity_refresh"
DEFAULT_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS = 6 * 60 * 60
MIN_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS = 15 * 60


class AutonomousOpportunityRefreshService:
    """Periodically refresh execution opportunities for active autonomous products.

    Discovery is deliberately separate from execution. This service may read public
    evidence, persist fresh opportunities and rebuild Distribution Plays, but it never
    publishes, joins a community, changes a profile or mutates a provider account.
    """

    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        audience_service: InMemoryAudienceIntelligenceService | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._audience_service = audience_service or audience_intelligence_service

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
            record = {
                "product_id": str(candidate.product_id),
                "status": "REFRESHED",
                "opportunity_count": distribution.opportunity_count,
                "play_count": plays.play_count,
                "ready_play_count": plays.ready_count,
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

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE)


autonomous_opportunity_refresh_service = AutonomousOpportunityRefreshService()
