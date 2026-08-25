from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.customer_channel_schemas import (
    CustomerChannelPreferencesUpdateRequest,
    CustomerChannelView,
)
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerResearchResponse
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_types import DistributionPlatform
from app.paid_provider_connections import paid_provider_connection_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

CHANNEL_PREFERENCES_KEY = "channel_preferences"
STAGED_META_CONNECTION_KEY = "meta_connection_staged"

CHANNEL_LABELS: dict[DistributionPlatform, str] = {
    DistributionPlatform.INSTAGRAM: "Instagram & Facebook",
    DistributionPlatform.TIKTOK: "TikTok",
    DistributionPlatform.REDDIT: "Reddit",
    DistributionPlatform.TELEGRAM: "Telegram",
}

DEFAULT_CHANNEL_MODES: dict[DistributionPlatform, str] = {
    DistributionPlatform.INSTAGRAM: "AUTO",
    DistributionPlatform.TIKTOK: "RESEARCH_ONLY",
    DistributionPlatform.REDDIT: "RESEARCH_ONLY",
    DistributionPlatform.TELEGRAM: "RESEARCH_ONLY",
}

# Customer-facing autonomous execution is intentionally narrower than the
# research surface. Additional platforms can become AUTO only after their
# customer authorization + execution + spend-control path is production-ready.
AUTONOMOUS_EXECUTION_PLATFORMS = frozenset({DistributionPlatform.INSTAGRAM})


class CustomerChannelService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def list(self, project_id: UUID, customer_token: str) -> list[CustomerChannelView]:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        preferences = self._preferences(project)
        metrics = self._metrics_by_platform(project)
        meta_connected = self._meta_connected(project)

        rows: list[CustomerChannelView] = []
        for platform in (
            DistributionPlatform.INSTAGRAM,
            DistributionPlatform.TIKTOK,
            DistributionPlatform.REDDIT,
            DistributionPlatform.TELEGRAM,
        ):
            item = metrics.get(platform)
            rows.append(
                CustomerChannelView(
                    platform=platform,
                    label=CHANNEL_LABELS[platform],
                    mode=preferences[platform],
                    autonomous_execution_available=(
                        platform in AUTONOMOUS_EXECUTION_PLATFORMS
                    ),
                    connected=(meta_connected if platform == DistributionPlatform.INSTAGRAM else None),
                    experiment_count=(item.experiment_count if item is not None else 0),
                    spend_usd=(item.spend if item is not None else 0.0),
                    paid_customers=(item.paid_users if item is not None else 0),
                    revenue_usd=(item.revenue if item is not None else 0.0),
                    cac_usd=(item.cac if item is not None else None),
                    roas=(item.roas if item is not None else None),
                )
            )
        return rows

    def update(
        self,
        project_id: UUID,
        customer_token: str,
        payload: CustomerChannelPreferencesUpdateRequest,
    ) -> list[CustomerChannelView]:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        preferences = self._preferences(project)
        for item in payload.channels:
            if (
                item.mode == "AUTO"
                and item.platform not in AUTONOMOUS_EXECUTION_PLATFORMS
            ):
                raise ValueError(
                    f"Autonomous execution is not available for {CHANNEL_LABELS[item.platform]} yet. "
                    "Use Research only or Off."
                )
            preferences[item.platform] = item.mode
        project[CHANNEL_PREFERENCES_KEY] = {
            platform.value: mode for platform, mode in preferences.items()
        }
        self._persist(project)
        return self.list(project_id, customer_token)

    def autonomous_platforms(self, project: dict) -> list[DistributionPlatform]:
        preferences = self._preferences(project)
        return [
            platform
            for platform in (
                DistributionPlatform.INSTAGRAM,
                DistributionPlatform.TIKTOK,
                DistributionPlatform.REDDIT,
                DistributionPlatform.TELEGRAM,
            )
            if preferences[platform] == "AUTO"
            and platform in AUTONOMOUS_EXECUTION_PLATFORMS
        ]

    def filter_research(
        self,
        project_id: UUID,
        customer_token: str,
        result: CustomerResearchResponse,
    ) -> CustomerResearchResponse:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        visible = [
            item
            for item in result.opportunities
            if not self.is_off(project, item.platform)
        ]
        return result.model_copy(update={"opportunities": visible})

    def is_off(self, project: dict, platform: str) -> bool:
        try:
            normalized = DistributionPlatform(str(platform).strip().upper())
        except ValueError:
            return False
        return self._preferences(project).get(normalized) == "OFF"

    def _preferences(self, project: dict) -> dict[DistributionPlatform, str]:
        raw = project.get(CHANNEL_PREFERENCES_KEY)
        result = dict(DEFAULT_CHANNEL_MODES)
        if not isinstance(raw, dict):
            return result
        for platform in result:
            mode = str(raw.get(platform.value) or "").upper()
            if mode in {"AUTO", "RESEARCH_ONLY", "OFF"}:
                if mode == "AUTO" and platform not in AUTONOMOUS_EXECUTION_PLATFORMS:
                    result[platform] = "RESEARCH_ONLY"
                else:
                    result[platform] = mode
        return result

    def _metrics_by_platform(self, project: dict) -> dict[DistributionPlatform, object]:
        product_id_raw = project.get("product_id")
        if not product_id_raw:
            return {}
        try:
            analytics = distribution_analytics_service.product_analytics(UUID(str(product_id_raw)))
        except (KeyError, ValueError):
            return {}
        rows: dict[DistributionPlatform, object] = {}
        for item in analytics.breakdowns:
            if item.dimension != "PLATFORM":
                continue
            try:
                platform = DistributionPlatform(item.key)
            except ValueError:
                continue
            rows[platform] = item
        return rows

    def _meta_connected(self, project: dict) -> bool:
        if isinstance(project.get(STAGED_META_CONNECTION_KEY), dict):
            return True
        product_id_raw = project.get("product_id")
        if not product_id_raw:
            return False
        try:
            return paid_provider_connection_service.get_meta(UUID(str(product_id_raw))) is not None
        except ValueError:
            return False

    def _persist(self, project: dict) -> None:
        project["updated_at"] = datetime.now(UTC).isoformat()
        self._store.put(
            CUSTOMER_PROJECT_NAMESPACE,
            str(project["id"]),
            project,
        )


customer_channel_service = CustomerChannelService()
