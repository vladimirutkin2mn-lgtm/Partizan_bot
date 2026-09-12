from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.channel_execution import ChannelCapability, PublisherMode
from app.config import Settings, get_settings
from app.customer_channel_schemas import (
    CustomerChannelCapabilityView,
    CustomerChannelPreferencesUpdateRequest,
    CustomerChannelView,
    CustomerPublisherModeView,
)
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerResearchResponse
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_types import DistributionPlatform
from app.growth_balance import GrowthBalanceService
from app.managed_distribution import managed_distribution_service
from app.paid_provider_connections import paid_provider_connection_service
from app.reddit_client_publishing import customer_reddit_client_publish_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_publishing import customer_telegram_client_publish_service

CHANNEL_PREFERENCES_KEY = "channel_preferences"
CHANNEL_PUBLISHER_MODES_KEY = "channel_publisher_modes"
SELECTED_ACQUISITION_CHANNEL_KEY = "selected_acquisition_channel"
STAGED_META_CONNECTION_KEY = "meta_connection_staged"

CHANNEL_LABELS: dict[DistributionPlatform, str] = {
    DistributionPlatform.INSTAGRAM: "Instagram & Facebook",
    DistributionPlatform.TIKTOK: "TikTok",
    DistributionPlatform.REDDIT: "Reddit",
    DistributionPlatform.TELEGRAM: "Telegram",
}

DEFAULT_CHANNEL_MODES: dict[DistributionPlatform, str] = {
    DistributionPlatform.INSTAGRAM: "RESEARCH_ONLY",
    DistributionPlatform.TIKTOK: "RESEARCH_ONLY",
    DistributionPlatform.REDDIT: "RESEARCH_ONLY",
    DistributionPlatform.TELEGRAM: "RESEARCH_ONLY",
}

DEFAULT_PUBLISHER_MODES: dict[DistributionPlatform, PublisherMode] = {
    DistributionPlatform.INSTAGRAM: PublisherMode.MANUAL,
    DistributionPlatform.TIKTOK: PublisherMode.MANUAL,
    DistributionPlatform.REDDIT: PublisherMode.MANUAL,
    DistributionPlatform.TELEGRAM: PublisherMode.MANUAL,
}

# Research is enabled by default, but no acquisition channel is pre-authorized
# for execution. A platform may become AUTO only after an explicit customer
# action and only when its execution + spend-control path is production-ready.
AUTONOMOUS_EXECUTION_PLATFORMS = frozenset({DistributionPlatform.INSTAGRAM})


class CustomerChannelService:
    def __init__(
        self,
        store: RuntimeStateStore | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._settings = settings or get_settings()
        self._balance = GrowthBalanceService(self._store)

    def list(self, project_id: UUID, customer_token: str) -> list[CustomerChannelView]:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        preferences = self._preferences(project)
        publisher_modes = self._publisher_modes(project)
        selected_platform = self._selected_platform(project)
        metrics = self._metrics_by_platform(project)
        meta_connected = self._meta_connected(project)
        telegram_connected = customer_telegram_client_publish_service.is_connected(project_id)
        reddit_connected = customer_reddit_client_publish_service.is_connected(project_id)
        settlement_ready = bool(
            self._balance.rail_view(project_id).get("settlement_ready")
        )

        rows: list[CustomerChannelView] = []
        for platform in (
            DistributionPlatform.INSTAGRAM,
            DistributionPlatform.TIKTOK,
            DistributionPlatform.REDDIT,
            DistributionPlatform.TELEGRAM,
        ):
            item = metrics.get(platform)
            execution_ready, execution_blocker = self._execution_readiness(
                platform,
                meta_connected=meta_connected,
                settlement_ready=settlement_ready,
            )
            rows.append(
                CustomerChannelView(
                    platform=platform,
                    label=CHANNEL_LABELS[platform],
                    mode=preferences[platform],
                    selected=platform == selected_platform,
                    publisher_mode=publisher_modes[platform],
                    publisher_modes=self._publisher_mode_options(platform),
                    capabilities=self._capabilities(
                        platform,
                        publisher_mode=publisher_modes[platform],
                        telegram_connected=telegram_connected,
                        reddit_connected=reddit_connected,
                        execution_ready=execution_ready,
                        execution_blocker=execution_blocker,
                    ),
                    autonomous_execution_available=self._autonomous_execution_available(platform),
                    execution_ready=execution_ready,
                    execution_blocker=execution_blocker,
                    connected=self._connected_value(
                        platform,
                        meta_connected=meta_connected,
                        telegram_connected=telegram_connected,
                        reddit_connected=reddit_connected,
                    ),
                    experiment_count=(item.experiment_count if item is not None else 0),
                    spend_usd=(item.spend if item is not None else 0.0),
                    paid_customers=(item.paid_users if item is not None else 0),
                    revenue_usd=(item.revenue if item is not None else 0.0),
                    cac_usd=(item.cac if item is not None else None),
                    roas=(item.roas if item is not None else None),
                )
            )
        return rows

    def select(
        self,
        project_id: UUID,
        customer_token: str,
        platform: DistributionPlatform,
    ) -> list[CustomerChannelView]:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        preferences = self._preferences(project)
        if preferences[platform] == "OFF":
            raise ValueError(
                f"{CHANNEL_LABELS[platform]} is turned off. Turn it back on before selecting it."
            )
        project[SELECTED_ACQUISITION_CHANNEL_KEY] = platform.value
        project["acquisition_channel_selected_at"] = datetime.now(UTC).isoformat()
        self._persist(project)
        return self.list(project_id, customer_token)

    def update(
        self,
        project_id: UUID,
        customer_token: str,
        payload: CustomerChannelPreferencesUpdateRequest,
    ) -> list[CustomerChannelView]:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        preferences = self._preferences(project)
        publisher_modes = self._publisher_modes(project)
        meta_connected = self._meta_connected(project)
        settlement_ready = bool(
            self._balance.rail_view(project_id).get("settlement_ready")
        )
        for item in payload.channels:
            if item.mode == "AUTO":
                execution_ready, blocker = self._execution_readiness(
                    item.platform,
                    meta_connected=meta_connected,
                    settlement_ready=settlement_ready,
                )
                if not execution_ready:
                    if item.platform not in AUTONOMOUS_EXECUTION_PLATFORMS:
                        raise ValueError(
                            f"Autonomous execution is not available for {CHANNEL_LABELS[item.platform]} yet. "
                            "Use Research only or Off."
                        )
                    raise ValueError(
                        f"Auto is not ready for {CHANNEL_LABELS[item.platform]}: {blocker}. "
                        "Keep Research only until the required execution path is ready."
                    )
            if item.publisher_mode is not None:
                option = next(
                    entry
                    for entry in self._publisher_mode_options(item.platform)
                    if entry.mode == item.publisher_mode
                )
                if not option.available:
                    raise ValueError(
                        f"{item.publisher_mode.value} publishing is not ready for "
                        f"{CHANNEL_LABELS[item.platform]}: {option.blocker}"
                    )
                publisher_modes[item.platform] = item.publisher_mode
            if item.mode is not None:
                preferences[item.platform] = item.mode
        selected_platform = self._selected_platform(project)
        if selected_platform is not None and preferences[selected_platform] == "OFF":
            project.pop(SELECTED_ACQUISITION_CHANNEL_KEY, None)
            project.pop("acquisition_channel_selected_at", None)
        project[CHANNEL_PREFERENCES_KEY] = {
            platform.value: mode for platform, mode in preferences.items()
        }
        project[CHANNEL_PUBLISHER_MODES_KEY] = {
            platform.value: mode.value for platform, mode in publisher_modes.items()
        }
        self._persist(project)
        return self.list(project_id, customer_token)

    def autonomous_platforms(self, project: dict) -> list[DistributionPlatform]:
        """Return customer AUTO intent that is currently eligible for runtime consideration."""

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
            and self._autonomous_execution_available(platform)
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

    @staticmethod
    def _selected_platform(project: dict) -> DistributionPlatform | None:
        raw = project.get(SELECTED_ACQUISITION_CHANNEL_KEY)
        if raw is None:
            return None
        try:
            return DistributionPlatform(str(raw).strip().upper())
        except ValueError:
            return None

    def _publisher_modes(self, project: dict) -> dict[DistributionPlatform, PublisherMode]:
        raw = project.get(CHANNEL_PUBLISHER_MODES_KEY)
        result = dict(DEFAULT_PUBLISHER_MODES)
        if not isinstance(raw, dict):
            return result
        for platform in result:
            try:
                mode = PublisherMode(str(raw.get(platform.value) or "").upper())
            except ValueError:
                continue
            available_modes = {
                item.mode for item in self._publisher_mode_options(platform) if item.available
            }
            if mode in available_modes:
                result[platform] = mode
        return result

    def _publisher_mode_options(
        self,
        platform: DistributionPlatform,
    ) -> list[CustomerPublisherModeView]:
        # PublisherMode models who performs an organic/community publish action.
        # It is intentionally separate from paid-provider connection/readiness.
        if platform == DistributionPlatform.TELEGRAM:
            blocker = customer_telegram_client_publish_service.readiness_blocker()
            client_owned_available = blocker is None
            client_owned_blocker = blocker
        elif platform == DistributionPlatform.REDDIT:
            blocker = customer_reddit_client_publish_service.readiness_blocker()
            client_owned_available = blocker is None
            client_owned_blocker = blocker
        else:
            client_owned_available = False
            client_owned_blocker = "publisher-mode execution is not implemented for this channel yet"
        managed_blocker = managed_distribution_service.readiness_blocker(platform)
        return [
            CustomerPublisherModeView(
                mode=PublisherMode.MANUAL,
                available=True,
            ),
            CustomerPublisherModeView(
                mode=PublisherMode.CLIENT_OWNED,
                available=client_owned_available,
                blocker=client_owned_blocker,
            ),
            CustomerPublisherModeView(
                mode=PublisherMode.PARTIZAN_MANAGED,
                available=managed_blocker is None,
                blocker=managed_blocker,
            ),
        ]

    def _capabilities(
        self,
        platform: DistributionPlatform,
        *,
        publisher_mode: PublisherMode,
        telegram_connected: bool,
        reddit_connected: bool,
        execution_ready: bool,
        execution_blocker: str | None,
    ) -> list[CustomerChannelCapabilityView]:
        measure_ready = False
        measure_blocker = "channel outcome adapter is not implemented yet"
        if publisher_mode == PublisherMode.PARTIZAN_MANAGED:
            managed_blocker = managed_distribution_service.readiness_blocker(platform)
            publish_ready = managed_blocker is None
            publish_blocker = managed_blocker
            measure_ready = publish_ready
            measure_blocker = managed_blocker
        elif platform == DistributionPlatform.INSTAGRAM:
            publish_ready = execution_ready
            publish_blocker = execution_blocker
        elif platform == DistributionPlatform.TELEGRAM:
            publish_ready, publish_blocker = self._telegram_publish_readiness(
                publisher_mode=publisher_mode,
                telegram_connected=telegram_connected,
            )
        elif platform == DistributionPlatform.REDDIT:
            publish_ready, publish_blocker = self._reddit_publish_readiness(
                publisher_mode=publisher_mode,
                reddit_connected=reddit_connected,
            )
            measure_ready = publish_ready
            measure_blocker = publish_blocker
        else:
            publish_ready = False
            publish_blocker = "channel publish adapter is not implemented yet"
        return [
            CustomerChannelCapabilityView(
                capability=ChannelCapability.SEARCH,
                ready=True,
            ),
            CustomerChannelCapabilityView(
                capability=ChannelCapability.DRAFT,
                ready=False,
                blocker="channel-native draft adapter is not implemented yet",
            ),
            CustomerChannelCapabilityView(
                capability=ChannelCapability.PUBLISH,
                ready=publish_ready,
                blocker=(None if publish_ready else publish_blocker),
            ),
            CustomerChannelCapabilityView(
                capability=ChannelCapability.MEASURE,
                ready=measure_ready,
                blocker=(None if measure_ready else measure_blocker),
            ),
        ]

    def _telegram_publish_readiness(
        self,
        *,
        publisher_mode: PublisherMode,
        telegram_connected: bool,
    ) -> tuple[bool, str | None]:
        blocker = customer_telegram_client_publish_service.readiness_blocker()
        if blocker is not None:
            return False, blocker
        if publisher_mode != PublisherMode.CLIENT_OWNED:
            return False, "select Client-owned as the Telegram publisher mode"
        if not telegram_connected:
            return False, "connect an authorised Telegram account first"
        return True, None

    def _reddit_publish_readiness(
        self,
        *,
        publisher_mode: PublisherMode,
        reddit_connected: bool,
    ) -> tuple[bool, str | None]:
        blocker = customer_reddit_client_publish_service.readiness_blocker()
        if blocker is not None:
            return False, blocker
        if publisher_mode != PublisherMode.CLIENT_OWNED:
            return False, "select Client-owned as the Reddit publisher mode"
        if not reddit_connected:
            return False, "connect an authorised Reddit account first"
        return True, None

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

    def _autonomous_execution_available(self, platform: DistributionPlatform) -> bool:
        if platform not in AUTONOMOUS_EXECUTION_PLATFORMS:
            return False
        if platform == DistributionPlatform.INSTAGRAM:
            return self._settings.meta_oauth_public_ready
        return True

    def _execution_readiness(
        self,
        platform: DistributionPlatform,
        *,
        meta_connected: bool,
        settlement_ready: bool,
    ) -> tuple[bool, str | None]:
        if platform not in AUTONOMOUS_EXECUTION_PLATFORMS:
            return False, "autonomous execution is not supported for this channel"
        if not self._autonomous_execution_available(platform):
            return False, "Meta customer connection is temporarily unavailable"
        if platform == DistributionPlatform.INSTAGRAM and not meta_connected:
            return False, "connect Meta first"
        if not settlement_ready:
            return False, "Partizan's paid-execution payment path is not ready"
        return True, None

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

    def _connected_value(
        self,
        platform: DistributionPlatform,
        *,
        meta_connected: bool,
        telegram_connected: bool,
        reddit_connected: bool,
    ) -> bool | None:
        if platform == DistributionPlatform.INSTAGRAM:
            return meta_connected
        if platform == DistributionPlatform.REDDIT:
            return reddit_connected
        if platform == DistributionPlatform.TELEGRAM:
            return telegram_connected
        return None

    def _persist(self, project: dict) -> None:
        project["updated_at"] = datetime.now(UTC).isoformat()
        self._store.put(
            CUSTOMER_PROJECT_NAMESPACE,
            str(project["id"]),
            project,
        )


customer_channel_service = CustomerChannelService()
