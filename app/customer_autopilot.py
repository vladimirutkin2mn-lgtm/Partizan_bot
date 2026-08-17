from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.audience_intelligence_service import audience_intelligence_service
from app.autonomy_overview import autonomy_overview_service
from app.autonomy_schemas import GrowthMandateStatus, GrowthMandateUpsertRequest
from app.autonomy_service import growth_mandate_service
from app.config import get_settings
from app.customer_funnel import (
    CUSTOMER_PROJECT_NAMESPACE,
    CustomerPaymentRequiredError,
    CustomerProjectNotFoundError,
    customer_funnel_service,
)
from app.customer_schemas import (
    CustomerAutopilotConfigureRequest,
    CustomerAutopilotDecisionView,
    CustomerAutopilotExperimentView,
    CustomerAutopilotOverview,
    CustomerMetaConnectionView,
)
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_play_service import distribution_play_service
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.paid_provider_connections import paid_provider_connection_service
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store


class CustomerAutopilotService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def prepare_checkout(self, project_id: UUID, customer_token: str) -> tuple[int, str | None]:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        if not project.get("launch_unlocked") or project.get("research_state") != "READY":
            raise CustomerPaymentRequiredError("Complete the Acquisition Plan before starting Autopilot")
        product_id = self._require_researched_product(project)
        self._require_paid_destination(product_id)
        if project.get("autopilot_subscription_status") == "ACTIVE":
            raise ValueError("Autopilot subscription is already active")
        generation = int(project.get("autopilot_checkout_generation") or 0) + 1
        project["autopilot_checkout_generation"] = generation
        self._persist(project)
        return generation, project.get("stripe_customer_id")

    def mark_checkout_pending(
        self,
        project_id: UUID,
        customer_token: str,
        session_id: str,
    ) -> None:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        project["autopilot_checkout_session_id"] = session_id
        project["autopilot_subscription_status"] = "CHECKOUT_PENDING"
        self._persist(project)

    def sync_subscription(
        self,
        project_id: UUID,
        *,
        subscription_id: str,
        stripe_status: str,
        stripe_customer_id: str | None = None,
        checkout_session_id: str | None = None,
    ) -> str:
        project = self._load(project_id)
        if project is None:
            raise CustomerProjectNotFoundError(project_id)
        normalized = self._subscription_state(stripe_status)
        project["autopilot_subscription_id"] = subscription_id
        project["autopilot_subscription_status"] = normalized
        if stripe_customer_id:
            project["stripe_customer_id"] = stripe_customer_id
        if checkout_session_id:
            project["autopilot_checkout_session_id"] = checkout_session_id
        project["autopilot_subscription_synced_at"] = datetime.now(UTC).isoformat()
        self._persist(project)
        if normalized != "ACTIVE":
            self._pause_for_billing(project)
        return normalized

    def configure(
        self,
        project_id: UUID,
        customer_token: str,
        payload: CustomerAutopilotConfigureRequest,
    ) -> CustomerAutopilotOverview:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        self._require_active_subscription(project)
        if not payload.confirm_autonomous_spend:
            raise ValueError("confirm_autonomous_spend=true is required")
        product_id = self._require_researched_product(project)
        product = self._require_paid_destination(product_id)
        distribution = audience_intelligence_service.get(product_id)
        try:
            distribution_play_service.get(product_id)
        except KeyError:
            distribution_play_service.generate(product, distribution)

        total = round(payload.marketing_budget_usd, 2)
        per_experiment = round(
            payload.max_autonomous_spend_per_experiment
            if payload.max_autonomous_spend_per_experiment is not None
            else min(total, max(25.0, total * 0.20)),
            2,
        )
        daily = round(
            payload.max_autonomous_spend_per_day
            if payload.max_autonomous_spend_per_day is not None
            else min(total, max(per_experiment, total / 7)),
            2,
        )
        if per_experiment > total or daily > total:
            raise ValueError("Autopilot spend limits cannot exceed the delegated marketing budget")

        mandate = growth_mandate_service.upsert(
            product_id,
            GrowthMandateUpsertRequest(
                total_budget_cap=total,
                target_max_cac=payload.target_max_cac,
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
        project["autopilot_marketing_budget_usd"] = total
        project["autopilot_target_max_cac"] = round(payload.target_max_cac, 2)
        project["autopilot_configured_at"] = datetime.now(UTC).isoformat()
        meta_connected = paid_provider_connection_service.get_meta(product_id) is not None
        if not meta_connected and mandate.status == GrowthMandateStatus.ACTIVE:
            growth_mandate_service.set_status(product_id, GrowthMandateStatus.PAUSED)
            project["autopilot_pause_reason"] = "SETUP"
        elif meta_connected and mandate.status == GrowthMandateStatus.ACTIVE:
            project["autopilot_pause_reason"] = None
        self._persist(project)
        return self.overview(project_id, customer_token)

    def set_status(
        self,
        project_id: UUID,
        customer_token: str,
        status: str,
    ) -> CustomerAutopilotOverview:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        product_id = self._require_researched_product(project)
        if status == "ACTIVE":
            self._require_active_subscription(project)
            self._require_paid_destination(product_id)
            if paid_provider_connection_service.get_meta(product_id) is None:
                raise ValueError("Connect Meta before activating Autopilot")
            growth_mandate_service.set_status(product_id, GrowthMandateStatus.ACTIVE)
            project["autopilot_pause_reason"] = None
        elif status == "PAUSED":
            growth_mandate_service.set_status(product_id, GrowthMandateStatus.PAUSED)
            project["autopilot_pause_reason"] = "CUSTOMER"
        else:
            raise ValueError("Unsupported Autopilot status")
        self._persist(project)
        return self.overview(project_id, customer_token)

    def meta_connected(self, project_id: UUID, customer_token: str) -> CustomerAutopilotOverview:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        product_id = self._require_researched_product(project)
        product = product_intake_service.get_product(product_id)
        try:
            mandate = growth_mandate_service.get(product_id)
        except KeyError:
            mandate = None
        if (
            mandate is not None
            and mandate.status == GrowthMandateStatus.PAUSED
            and project.get("autopilot_pause_reason") == "SETUP"
            and project.get("autopilot_subscription_status") == "ACTIVE"
            and bool(product.reference_links)
        ):
            growth_mandate_service.set_status(product_id, GrowthMandateStatus.ACTIVE)
            project["autopilot_pause_reason"] = None
            self._persist(project)
        return self.overview(project_id, customer_token)

    def overview(self, project_id: UUID, customer_token: str) -> CustomerAutopilotOverview:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        product_id = self._require_researched_product(project)
        product = product_intake_service.get_product(product_id)
        analytics = distribution_analytics_service.product_analytics(product_id)
        try:
            autonomy = autonomy_overview_service.get(product_id)
            mandate = autonomy.mandate
        except (KeyError, ValueError):
            autonomy = None
            mandate = None
        connection = paid_provider_connection_service.get_meta(product_id)
        subscription_status = str(project.get("autopilot_subscription_status") or "INACTIVE")
        blockers: list[str] = []
        if subscription_status != "ACTIVE":
            blockers.append("Autopilot subscription is not active")
        if not product.reference_links:
            blockers.append("Website or landing page is required for paid traffic")
        if mandate is None:
            blockers.append("Marketing budget is not delegated yet")
        if connection is None:
            blockers.append("Meta is not connected")
        if mandate is not None and mandate.status != GrowthMandateStatus.ACTIVE:
            blockers.append(f"Autopilot is {mandate.status.value.lower()}")

        budget = round(mandate.total_budget_cap, 2) if mandate is not None else 0.0
        spent = round(analytics.total_spend, 2)
        remaining = max(round(budget - spent, 2), 0.0)
        settings = get_settings()
        fee = round(spent * settings.partizan_managed_spend_fee_pct / 100, 2)
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
            subscription_status=subscription_status,
            autopilot_status=mandate.status.value if mandate is not None else "NOT_CONFIGURED",
            setup_complete=not blockers,
            blockers=blockers,
            marketing_budget_usd=budget,
            spent_usd=spent,
            remaining_budget_usd=remaining,
            paid_customers=analytics.total_paid_users,
            revenue_usd=round(analytics.total_revenue, 2),
            cac_usd=analytics.blended_cac,
            roas=analytics.blended_roas,
            managed_spend_fee_pct=settings.partizan_managed_spend_fee_pct,
            estimated_managed_fee_usd=fee,
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

    def _pause_for_billing(self, project: dict) -> None:
        product_id_raw = project.get("product_id")
        if not product_id_raw:
            return
        product_id = UUID(str(product_id_raw))
        try:
            mandate = growth_mandate_service.get(product_id)
        except KeyError:
            return
        if mandate.status == GrowthMandateStatus.ACTIVE:
            growth_mandate_service.set_status(product_id, GrowthMandateStatus.PAUSED)
            project["autopilot_pause_reason"] = "BILLING"
            self._persist(project)

    @staticmethod
    def _subscription_state(stripe_status: str) -> str:
        normalized = stripe_status.strip().lower()
        if normalized in {"active", "trialing"}:
            return "ACTIVE"
        if normalized in {"past_due", "unpaid", "paused"}:
            return "PAST_DUE"
        if normalized in {"canceled", "cancelled"}:
            return "CANCELLED"
        return "CHECKOUT_PENDING"

    @staticmethod
    def _require_active_subscription(project: dict) -> None:
        if project.get("autopilot_subscription_status") != "ACTIVE":
            raise CustomerPaymentRequiredError("Activate the Autopilot subscription first")

    @staticmethod
    def _require_researched_product(project: dict) -> UUID:
        product_id_raw = project.get("product_id")
        if project.get("research_state") != "READY" or not product_id_raw:
            raise ValueError("Complete acquisition research before configuring Autopilot")
        return UUID(str(product_id_raw))

    @staticmethod
    def _require_paid_destination(product_id: UUID):
        product = product_intake_service.get_product(product_id)
        if not product.reference_links:
            raise ValueError("Add a website or landing page before starting paid Autopilot")
        return product

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
