from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.audience_intelligence_service import audience_intelligence_service
from app.autonomy_overview import autonomy_overview_service
from app.autonomy_schemas import GrowthMandateStatus, GrowthMandateUpsertRequest
from app.autonomy_service import growth_mandate_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import (
    CustomerAutopilotConfigureRequest,
    CustomerAutopilotDecisionView,
    CustomerAutopilotExperimentView,
    CustomerAutopilotOverview,
    CustomerGrowthBalanceView,
    CustomerMetaConnectionView,
)
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_play_service import distribution_play_service
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.growth_balance import GrowthBalanceService, GrowthBalanceSummary
from app.paid_provider_connections import (
    PaidProviderConnectionCreateRequest,
    paid_provider_connection_service,
)
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

STAGED_META_CONNECTION_KEY = "meta_connection_staged"


class CustomerAutopilotService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()
        self._balance = GrowthBalanceService(self._store)

    def configure(
        self,
        project_id: UUID,
        customer_token: str,
        payload: CustomerAutopilotConfigureRequest,
    ) -> CustomerAutopilotOverview:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        if not payload.confirm_autonomous_spend:
            raise ValueError("confirm_autonomous_spend=true is required")

        project["autopilot_target_max_cac"] = round(payload.target_max_cac, 2)
        project["autopilot_spend_confirmed"] = True
        project["autopilot_configured_at"] = datetime.now(UTC).isoformat()
        self._persist(project)

        product_id_raw = project.get("product_id")
        research_ready = project.get("research_state") == "READY" and bool(product_id_raw)
        if research_ready:
            product_id = UUID(str(product_id_raw))
            self._materialize_staged_meta(project, product_id)
            self._ensure_mandate_if_ready(
                project_id,
                project,
                product_id,
                force_update=True,
            )
        return self.overview(project_id, customer_token)

    def set_status(
        self,
        project_id: UUID,
        customer_token: str,
        status: str,
    ) -> CustomerAutopilotOverview:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        product_id = self._require_researched_product(project)
        self._materialize_staged_meta(project, product_id)
        self._ensure_mandate_if_ready(project_id, project, product_id)
        if status == "ACTIVE":
            self._require_paid_destination(product_id)
            analytics = distribution_analytics_service.product_analytics(product_id)
            balance = self._balance.summary(project_id, analytics.total_spend)
            if balance.remaining_acquisition_capacity_usd <= 0:
                raise ValueError("Fund the Growth Balance before activating Autopilot")
            if not balance.settlement_ready:
                raise ValueError("Growth Balance funding is not available for paid activation yet")
            if paid_provider_connection_service.get_meta(product_id) is None:
                raise ValueError("Connect Meta before activating paid acquisition")
            self._balance.activate_rail(project_id)
            growth_mandate_service.set_status(product_id, GrowthMandateStatus.ACTIVE)
            project["autopilot_pause_reason"] = None
        elif status == "PAUSED":
            growth_mandate_service.set_status(product_id, GrowthMandateStatus.PAUSED)
            project["autopilot_pause_reason"] = "CUSTOMER"
            self._persist(project)
            self._balance.pause_rail(project_id, "CUSTOMER")
            return self.overview(project_id, customer_token)
        else:
            raise ValueError("Unsupported Autopilot status")
        self._persist(project)
        return self.overview(project_id, customer_token)

    def meta_connected(self, project_id: UUID, customer_token: str) -> CustomerAutopilotOverview:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        product_id_raw = project.get("product_id")
        research_ready = project.get("research_state") == "READY" and bool(product_id_raw)
        if not research_ready:
            return self.overview(project_id, customer_token)

        product_id = UUID(str(product_id_raw))
        self._materialize_staged_meta(project, product_id)
        self._ensure_mandate_if_ready(project_id, project, product_id)
        product = product_intake_service.get_product(product_id)
        analytics = distribution_analytics_service.product_analytics(product_id)
        balance = self._balance.summary(project_id, analytics.total_spend)
        try:
            mandate = growth_mandate_service.get(product_id)
        except KeyError:
            mandate = None
        can_activate = (
            mandate is not None
            and mandate.status == GrowthMandateStatus.PAUSED
            and bool(product.reference_links)
            and balance.remaining_acquisition_capacity_usd > 0
            and balance.settlement_ready
            and paid_provider_connection_service.get_meta(product_id) is not None
        )
        if can_activate:
            self._balance.activate_rail(project_id)
            growth_mandate_service.set_status(product_id, GrowthMandateStatus.ACTIVE)
            project["autopilot_pause_reason"] = None
            self._persist(project)
        elif mandate is not None and not balance.settlement_ready:
            project["autopilot_pause_reason"] = "FUNDING"
            self._persist(project)
        return self.overview(project_id, customer_token)

    def overview(self, project_id: UUID, customer_token: str) -> CustomerAutopilotOverview:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        product_id_raw = project.get("product_id")
        research_ready = project.get("research_state") == "READY" and bool(product_id_raw)
        guardrails_saved = bool(
            project.get("autopilot_spend_confirmed")
            and project.get("autopilot_target_max_cac")
        )

        if not research_ready:
            balance = self._balance.summary(project_id, 0.0)
            staged_meta = self._staged_meta_view(project)
            blockers: list[str] = []
            if not staged_meta.connected:
                blockers.append("Meta access is not connected")
            if not guardrails_saved:
                blockers.append("Maximum CAC and autonomous-spend guardrails are not saved")
            if balance.funded_usd <= 0:
                blockers.append("Growth Balance is not funded")
            if not balance.settlement_ready:
                blockers.append("Growth Balance funding is not available yet")
            blockers.append(
                "Acquisition research starts automatically after Growth Balance funding"
                if balance.funded_usd <= 0
                else "Partizan is mapping the audience and acquisition strategy"
            )
            return CustomerAutopilotOverview(
                project_id=project_id,
                product_id=None,
                autopilot_status="RESEARCHING",
                setup_complete=False,
                blockers=blockers,
                growth_balance=self._growth_view(balance),
                paid_customers=0,
                revenue_usd=0.0,
                cac_usd=None,
                roas=None,
                meta=staged_meta,
                running_experiments=[],
                waiting_experiments=[],
                recent_decisions=[],
            )

        product_id = UUID(str(product_id_raw))
        self._materialize_staged_meta(project, product_id)
        self._ensure_mandate_if_ready(project_id, project, product_id)
        product = product_intake_service.get_product(product_id)
        analytics = distribution_analytics_service.product_analytics(product_id)
        balance = self._balance.summary(project_id, analytics.total_spend)
        try:
            autonomy = autonomy_overview_service.get(product_id)
            mandate = autonomy.mandate
        except (KeyError, ValueError):
            autonomy = None
            mandate = None
        connection = paid_provider_connection_service.get_meta(product_id)

        blockers = []
        if not product.reference_links:
            blockers.append("Website or landing page is required for paid traffic")
        if balance.funded_usd <= 0:
            blockers.append("Growth Balance is not funded")
        elif balance.remaining_acquisition_capacity_usd <= 0:
            blockers.append("Growth Balance has no acquisition capacity remaining")
        if not balance.settlement_ready:
            blockers.append("Growth Balance funding is not available yet")
        if not guardrails_saved:
            blockers.append("Maximum CAC and autonomous-spend guardrails are not saved")
        elif mandate is None and balance.funded_usd > 0:
            blockers.append("Partizan is applying the saved guardrails")
        if connection is None:
            blockers.append("Meta access is not connected")
        if mandate is not None and mandate.status != GrowthMandateStatus.ACTIVE:
            blockers.append(f"Autopilot is {mandate.status.value.lower()}")

        running = (
            []
            if autonomy is None
            else [self._experiment(item) for item in autonomy.running_experiments]
        )
        waiting = (
            []
            if autonomy is None
            else [self._experiment(item) for item in autonomy.waiting_approval]
        )
        decisions = [] if autonomy is None else [
            CustomerAutopilotDecisionView(
                recorded_at=item.recorded_at,
                kind=item.kind.value,
                outcome=item.outcome,
                decision=item.decision,
                reasons=item.reasons,
            )
            for item in autonomy.recent_decisions[:12]
        ]
        return CustomerAutopilotOverview(
            project_id=project_id,
            product_id=product_id,
            autopilot_status=mandate.status.value if mandate is not None else "NOT_CONFIGURED",
            setup_complete=not blockers,
            blockers=blockers,
            growth_balance=self._growth_view(balance),
            paid_customers=analytics.total_paid_users,
            revenue_usd=round(analytics.total_revenue, 2),
            cac_usd=analytics.blended_cac,
            roas=analytics.blended_roas,
            meta=CustomerMetaConnectionView(
                connected=connection is not None,
                ad_account_id=connection.ad_account_id if connection else None,
                page_id=connection.page_id if connection else None,
                instagram_actor_id=connection.instagram_actor_id if connection else None,
                country_codes=list(connection.country_codes) if connection else [],
            ),
            running_experiments=running,
            waiting_experiments=waiting,
            recent_decisions=decisions,
        )

    def _ensure_mandate_if_ready(
        self,
        project_id: UUID,
        project: dict,
        product_id: UUID,
        *,
        force_update: bool = False,
    ):
        if not project.get("autopilot_spend_confirmed"):
            return None
        target_max_cac = project.get("autopilot_target_max_cac")
        if target_max_cac is None:
            return None
        try:
            existing = growth_mandate_service.get(product_id)
        except KeyError:
            existing = None
        if existing is not None and not force_update:
            return existing

        product = product_intake_service.get_product(product_id)
        if not product.reference_links:
            return existing
        analytics = distribution_analytics_service.product_analytics(product_id)
        balance = self._balance.summary(project_id, analytics.total_spend)
        if balance.funded_usd <= 0 or balance.remaining_acquisition_capacity_usd <= 0:
            return existing

        distribution = audience_intelligence_service.get(product_id)
        try:
            distribution_play_service.get(product_id)
        except KeyError:
            distribution_play_service.generate(product, distribution)

        total_cap = round(balance.acquisition_capacity_usd, 2)
        remaining = round(balance.remaining_acquisition_capacity_usd, 2)
        per_experiment = round(min(remaining, max(1.0, total_cap * 0.20)), 2)
        daily = round(min(remaining, max(per_experiment, total_cap / 7)), 2)
        mandate = growth_mandate_service.upsert(
            product_id,
            GrowthMandateUpsertRequest(
                total_budget_cap=total_cap,
                target_max_cac=float(target_max_cac),
                max_autonomous_spend_per_experiment=per_experiment,
                max_autonomous_spend_per_day=daily,
                max_concurrent_running_experiments=2,
                allowed_platforms=[DistributionPlatform.INSTAGRAM],
                allowed_actions=[DistributionActionType.PAID_CAMPAIGN],
                autonomous_prepare=True,
                autonomous_approve=True,
                autonomous_paid_activation=True,
                approval_threshold=None,
            ),
        )

        meta_connected = paid_provider_connection_service.get_meta(product_id) is not None
        pause_reason: str | None = None
        if not meta_connected:
            pause_reason = "SETUP"
        elif not balance.settlement_ready:
            pause_reason = "FUNDING"
        if pause_reason is not None and mandate.status == GrowthMandateStatus.ACTIVE:
            growth_mandate_service.set_status(product_id, GrowthMandateStatus.PAUSED)
            self._balance.pause_rail(project_id, pause_reason)
        project["autopilot_pause_reason"] = pause_reason
        self._persist(project)
        return mandate

    def _materialize_staged_meta(self, project: dict, product_id: UUID):
        staged = project.get(STAGED_META_CONNECTION_KEY)
        if not isinstance(staged, dict):
            return paid_provider_connection_service.get_meta(product_id)
        connection = paid_provider_connection_service.upsert_meta(
            product_id,
            PaidProviderConnectionCreateRequest(
                ad_account_id=str(staged["ad_account_id"]),
                page_id=str(staged["page_id"]),
                instagram_actor_id=(
                    str(staged["instagram_actor_id"])
                    if staged.get("instagram_actor_id")
                    else None
                ),
                access_token_env=str(staged["access_token_env"]),
                api_version=str(staged["api_version"]),
                country_codes=[str(code) for code in staged.get("country_codes", [])],
                default_image_url=None,
            ),
        )
        project.pop(STAGED_META_CONNECTION_KEY, None)
        self._persist(project)
        return connection

    @staticmethod
    def _staged_meta_view(project: dict) -> CustomerMetaConnectionView:
        staged = project.get(STAGED_META_CONNECTION_KEY)
        if not isinstance(staged, dict):
            return CustomerMetaConnectionView(connected=False)
        return CustomerMetaConnectionView(
            connected=True,
            ad_account_id=str(staged.get("ad_account_id") or "") or None,
            page_id=str(staged.get("page_id") or "") or None,
            instagram_actor_id=(
                str(staged.get("instagram_actor_id"))
                if staged.get("instagram_actor_id")
                else None
            ),
            country_codes=[str(code) for code in staged.get("country_codes", [])],
        )

    @staticmethod
    def _require_researched_product(project: dict) -> UUID:
        product_id_raw = project.get("product_id")
        if project.get("research_state") != "READY" or not product_id_raw:
            raise ValueError("Partizan must finish internal acquisition research first")
        return UUID(str(product_id_raw))

    @staticmethod
    def _require_paid_destination(product_id: UUID):
        product = product_intake_service.get_product(product_id)
        if not product.reference_links:
            raise ValueError("Add a website or landing page before starting paid Autopilot")
        return product

    @staticmethod
    def _growth_view(balance: GrowthBalanceSummary) -> CustomerGrowthBalanceView:
        return CustomerGrowthBalanceView(
            funded_usd=balance.funded_usd,
            acquisition_spend_usd=balance.acquisition_spend_usd,
            management_fee_pct=balance.management_fee_pct,
            management_fee_usd=balance.management_fee_usd,
            used_usd=balance.used_usd,
            available_usd=balance.available_usd,
            acquisition_capacity_usd=balance.acquisition_capacity_usd,
            remaining_acquisition_capacity_usd=balance.remaining_acquisition_capacity_usd,
            settlement_ready=balance.settlement_ready,
            settlement_status=balance.settlement_status,
        )

    @staticmethod
    def _experiment(item) -> CustomerAutopilotExperimentView:
        return CustomerAutopilotExperimentView(
            experiment_id=item.experiment_id,
            platform=item.platform,
            action_type=item.action_type,
            status=item.experiment_status,
            budget_cap=item.budget_cap,
        )

    def _load(self, project_id: UUID) -> dict | None:
        return self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))

    def _persist(self, project: dict) -> None:
        project["updated_at"] = datetime.now(UTC).isoformat()
        self._store.put(CUSTOMER_PROJECT_NAMESPACE, project["id"], project)


customer_autopilot_service = CustomerAutopilotService()
