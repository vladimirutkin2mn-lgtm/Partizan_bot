from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.audience_intelligence_service import audience_intelligence_service
from app.autonomy_overview import autonomy_overview_service
from app.autonomy_schemas import GrowthMandateStatus, GrowthMandateUpsertRequest
from app.autonomy_service import growth_mandate_service
from app.customer_channels import customer_channel_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_paid_campaign_lifecycle import (
    AUTOPILOT_CUSTOMER_PAUSE_REASON,
    customer_paid_campaign_lifecycle_service,
)
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
AUTOPILOT_PROVIDER_PAUSE_REASONS = {
    "CUSTOMER": AUTOPILOT_CUSTOMER_PAUSE_REASON,
    "CHANNELS": "AUTOPILOT_CHANNELS_PAUSE",
    "FUNDING": "AUTOPILOT_FUNDING_PAUSE",
    "SETUP": "AUTOPILOT_SETUP_PAUSE",
}
AUTOPILOT_AUTOMATIC_PAUSE_REASONS = frozenset({"CHANNELS", "FUNDING", "SETUP"})


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

    def refresh_channel_policy(
        self,
        project_id: UUID,
        customer_token: str,
    ) -> CustomerAutopilotOverview:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
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

    def reconcile_safety_policy(self, product_id: UUID | None = None) -> int:
        reconciled = 0
        for project in self._store.list_namespace(CUSTOMER_PROJECT_NAMESPACE):
            product_id_raw = project.get("product_id")
            if project.get("research_state") != "READY" or not product_id_raw:
                continue
            candidate_product_id = UUID(str(product_id_raw))
            if product_id is not None and candidate_product_id != product_id:
                continue
            try:
                project_id = UUID(str(project["id"]))
            except (KeyError, ValueError):
                continue
            self._ensure_mandate_if_ready(project_id, project, candidate_product_id)
            reconciled += 1
        return reconciled

    def set_status(
        self,
        project_id: UUID,
        customer_token: str,
        status: str,
    ) -> CustomerAutopilotOverview:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        product_id = self._require_researched_product(project)
        auto_platforms = customer_channel_service.autonomous_platforms(project)
        previous_pause_reason = str(project.get("autopilot_pause_reason") or "") or None
        provider_pause_reason = self._provider_pause_reason(previous_pause_reason)
        self._materialize_staged_meta(project, product_id)
        self._ensure_mandate_if_ready(project_id, project, product_id)
        if status == "ACTIVE":
            if not auto_platforms:
                raise ValueError("Enable at least one Auto channel before resuming Partizan")
            self._require_acquisition_destination(product_id)
            paid_auto = DistributionPlatform.INSTAGRAM in auto_platforms
            analytics = distribution_analytics_service.product_analytics(product_id)
            balance = self._balance.summary(project_id, analytics.total_spend)
            if paid_auto and balance.remaining_acquisition_capacity_usd <= 0:
                raise ValueError("Fund the Growth Balance before activating Meta Autopilot")
            if paid_auto and not balance.settlement_ready:
                raise ValueError("Paid execution payment path is not ready yet")
            if (
                paid_auto
                and paid_provider_connection_service.get_meta(product_id) is None
            ):
                raise ValueError("Connect Meta before activating Meta acquisition")

            provider_resume = None
            if paid_auto and provider_pause_reason is not None:
                try:
                    provider_resume = customer_paid_campaign_lifecycle_service.resume_product(
                        product_id,
                        expected_reason=provider_pause_reason,
                    )
                except (KeyError, RuntimeError, ValueError) as exc:
                    recovery = customer_paid_campaign_lifecycle_service.pause_product(
                        product_id,
                        reason=provider_pause_reason,
                    )
                    if recovery.requires_reconciliation:
                        raise ValueError(
                            "Paid campaigns require reconciliation before Autopilot can resume"
                        ) from exc
                    raise ValueError(
                        "Paid campaigns were returned to a confirmed pause; retry Autopilot resume"
                    ) from exc

                accounted_action_ids = set(
                    provider_resume.preserved_pause_action_ids
                    + provider_resume.resumed_action_ids
                    + provider_resume.reconciliation_action_ids
                    + provider_resume.rollback_unknown_action_ids
                )
                if (
                    provider_resume.requires_reconciliation
                    or len(accounted_action_ids) != provider_resume.candidate_count
                ):
                    recovery = customer_paid_campaign_lifecycle_service.pause_product(
                        product_id,
                        reason=provider_pause_reason,
                    )
                    if recovery.requires_reconciliation:
                        raise ValueError(
                            "Paid campaigns require reconciliation before Autopilot can resume"
                        )
                    raise ValueError(
                        "Paid campaigns were returned to a confirmed pause; retry Autopilot resume"
                    )

            try:
                if paid_auto:
                    self._balance.activate_rail(project_id)
                growth_mandate_service.set_status(product_id, GrowthMandateStatus.ACTIVE)
                project["autopilot_pause_reason"] = None
                self._persist(project)
            except (KeyError, RuntimeError, ValueError) as exc:
                rollback_requires_reconciliation = False
                rollback_pause_reason = previous_pause_reason or "CUSTOMER"
                rollback_provider_reason = (
                    provider_pause_reason or AUTOPILOT_CUSTOMER_PAUSE_REASON
                )
                try:
                    growth_mandate_service.set_status(product_id, GrowthMandateStatus.PAUSED)
                except (KeyError, RuntimeError, ValueError):
                    rollback_requires_reconciliation = True
                if paid_auto:
                    try:
                        self._balance.pause_rail(project_id, rollback_pause_reason)
                    except (KeyError, RuntimeError, ValueError):
                        rollback_requires_reconciliation = True
                if paid_auto and provider_resume is not None and provider_resume.resumed_action_ids:
                    rollback = customer_paid_campaign_lifecycle_service.repause_actions(
                        product_id,
                        provider_resume.resumed_action_ids,
                        reason=rollback_provider_reason,
                    )
                    rollback_requires_reconciliation = (
                        rollback_requires_reconciliation or rollback.requires_reconciliation
                    )
                project["autopilot_pause_reason"] = rollback_pause_reason
                try:
                    self._persist(project)
                except RuntimeError:
                    rollback_requires_reconciliation = True
                if rollback_requires_reconciliation:
                    raise ValueError(
                        "Autopilot resume failed and paid execution rollback requires reconciliation"
                    ) from exc
                raise ValueError("Autopilot resume failed safely; retry when setup is ready") from exc
            return self.overview(project_id, customer_token)

        if status == "PAUSED":
            pause_failed = False
            project["autopilot_pause_reason"] = "CUSTOMER"
            try:
                growth_mandate_service.set_status(product_id, GrowthMandateStatus.PAUSED)
            except (KeyError, RuntimeError, ValueError):
                pause_failed = True
            try:
                self._balance.pause_rail(project_id, "CUSTOMER")
            except (KeyError, RuntimeError, ValueError):
                pause_failed = True
            try:
                provider_pause = customer_paid_campaign_lifecycle_service.pause_product(
                    product_id,
                    reason=AUTOPILOT_CUSTOMER_PAUSE_REASON,
                )
            except (KeyError, RuntimeError, ValueError):
                provider_pause = None
                pause_failed = True
            if provider_pause is not None and provider_pause.requires_reconciliation:
                pause_failed = True
            try:
                self._persist(project)
            except RuntimeError:
                pause_failed = True
            if pause_failed:
                raise ValueError(
                    "Autopilot is fail-closed, but paid provider state requires reconciliation"
                )
            return self.overview(project_id, customer_token)

        raise ValueError("Unsupported Autopilot status")

    def meta_connected(self, project_id: UUID, customer_token: str) -> CustomerAutopilotOverview:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        product_id_raw = project.get("product_id")
        research_ready = project.get("research_state") == "READY" and bool(product_id_raw)
        if not research_ready:
            return self.overview(project_id, customer_token)

        product_id = UUID(str(product_id_raw))
        auto_platforms = customer_channel_service.autonomous_platforms(project)
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
            DistributionPlatform.INSTAGRAM in auto_platforms
            and mandate is not None
            and mandate.status == GrowthMandateStatus.PAUSED
            and project.get("autopilot_pause_reason") is None
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
            self._apply_automatic_pause(
                project_id,
                project,
                product_id,
                reason=self._effective_automatic_pause_reason(project, "FUNDING"),
            )
        return self.overview(project_id, customer_token)

    def overview(self, project_id: UUID, customer_token: str) -> CustomerAutopilotOverview:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        product_id_raw = project.get("product_id")
        research_ready = project.get("research_state") == "READY" and bool(product_id_raw)
        guardrails_saved = bool(
            project.get("autopilot_spend_confirmed")
            and project.get("autopilot_target_max_cac")
        )
        auto_platforms = customer_channel_service.autonomous_platforms(project)

        if not research_ready:
            balance = self._balance.summary(project_id, 0.0)
            staged_meta = self._staged_meta_view(project)
            blockers: list[str] = []
            if not auto_platforms:
                blockers.append("No autonomous execution channel is enabled")
            if (
                DistributionPlatform.INSTAGRAM in auto_platforms
                and not staged_meta.connected
            ):
                blockers.append("Meta access is not connected")
            if not guardrails_saved:
                blockers.append("Maximum CAC and autonomous-spend guardrails are not saved")
            if balance.funded_usd <= 0:
                blockers.append("Growth Balance is not funded")
            if not balance.settlement_ready:
                blockers.append("Paid execution payment path is not ready yet")
            blockers.append(
                "Partizan is researching before spend; add acquisition budget only for a concrete paid move"
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
        if not auto_platforms:
            blockers.append("No autonomous execution channel is enabled")
        if not product.reference_links:
            blockers.append("Website or landing page is required for paid traffic")
        if balance.funded_usd <= 0:
            blockers.append("Growth Balance is not funded")
        elif balance.remaining_acquisition_capacity_usd <= 0:
            blockers.append("Growth Balance has no acquisition capacity remaining")
        if not balance.settlement_ready:
            blockers.append("Paid execution payment path is not ready yet")
        if not guardrails_saved:
            blockers.append("Maximum CAC and autonomous-spend guardrails are not saved")
        elif mandate is None and balance.funded_usd > 0 and auto_platforms:
            blockers.append("Partizan is applying the saved guardrails")
        if DistributionPlatform.INSTAGRAM in auto_platforms and connection is None:
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
                ad_account_name=(
                    str(project.get("meta_ad_account_name") or "").strip() or None
                ),
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

        auto_platforms = customer_channel_service.autonomous_platforms(project)
        if project.get("autopilot_pause_reason") == "CUSTOMER":
            return existing

        if not auto_platforms:
            if existing is not None:
                self._apply_automatic_pause(
                    project_id,
                    project,
                    product_id,
                    reason=self._effective_automatic_pause_reason(project, "CHANNELS"),
                )
                return growth_mandate_service.get(product_id)
            return existing

        product = product_intake_service.get_product(product_id)
        if not product.reference_links:
            return existing
        analytics = distribution_analytics_service.product_analytics(product_id)
        balance = self._balance.summary(project_id, analytics.total_spend)
        safety_reason: str | None = None
        if (
            DistributionPlatform.INSTAGRAM in auto_platforms
            and paid_provider_connection_service.get_meta(product_id) is None
        ):
            safety_reason = "SETUP"
        elif balance.funded_usd <= 0 or balance.remaining_acquisition_capacity_usd <= 0:
            safety_reason = "FUNDING"
        elif not balance.settlement_ready:
            safety_reason = "FUNDING"
        if safety_reason is not None:
            if existing is not None:
                self._apply_automatic_pause(
                    project_id,
                    project,
                    product_id,
                    reason=self._effective_automatic_pause_reason(project, safety_reason),
                )
                return growth_mandate_service.get(product_id)
            return existing

        if existing is not None and not force_update:
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
                allowed_platforms=auto_platforms,
                allowed_actions=[DistributionActionType.PAID_CAMPAIGN],
                autonomous_prepare=True,
                autonomous_approve=True,
                autonomous_paid_activation=True,
                approval_threshold=None,
            ),
        )

        current_pause_reason = str(project.get("autopilot_pause_reason") or "") or None
        if current_pause_reason not in AUTOPILOT_AUTOMATIC_PAUSE_REASONS:
            project["autopilot_pause_reason"] = None
        self._persist(project)
        return mandate

    def _apply_automatic_pause(
        self,
        project_id: UUID,
        project: dict,
        product_id: UUID,
        *,
        reason: str,
    ) -> None:
        if reason not in AUTOPILOT_AUTOMATIC_PAUSE_REASONS:
            raise ValueError("Unsupported automatic Autopilot pause reason")
        provider_reason = AUTOPILOT_PROVIDER_PAUSE_REASONS[reason]
        pause_failed = False
        project["autopilot_pause_reason"] = reason
        try:
            growth_mandate_service.set_status(product_id, GrowthMandateStatus.PAUSED)
        except (KeyError, RuntimeError, ValueError):
            pause_failed = True
        try:
            self._balance.pause_rail(project_id, reason)
        except (KeyError, RuntimeError, ValueError):
            pause_failed = True
        try:
            provider_pause = customer_paid_campaign_lifecycle_service.pause_product(
                product_id,
                reason=provider_reason,
            )
        except (KeyError, RuntimeError, ValueError):
            provider_pause = None
            pause_failed = True
        if provider_pause is not None and provider_pause.requires_reconciliation:
            pause_failed = True
        try:
            self._persist(project)
        except RuntimeError:
            pause_failed = True
        if pause_failed:
            raise ValueError(
                "Autopilot safety pause is fail-closed, but paid provider state requires reconciliation"
            )

    @staticmethod
    def _effective_automatic_pause_reason(project: dict, fallback: str) -> str:
        current = str(project.get("autopilot_pause_reason") or "")
        if current in AUTOPILOT_AUTOMATIC_PAUSE_REASONS:
            return current
        return fallback

    @staticmethod
    def _provider_pause_reason(project_pause_reason: str | None) -> str | None:
        if project_pause_reason is None:
            return None
        return AUTOPILOT_PROVIDER_PAUSE_REASONS.get(project_pause_reason)

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
            ad_account_name=(
                str(project.get("meta_ad_account_name") or "").strip() or None
            ),
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
