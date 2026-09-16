from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.audience_intelligence_service import audience_intelligence_service
from app.customer_channels import customer_channel_service
from app.customer_funnel import customer_funnel_service
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_schemas import DistributionPlayView, DistributionTacticClass
from app.distribution_types import DistributionActionType
from app.growth_balance import growth_balance_service
from app.growth_balance_jit_funding import growth_balance_jit_funding_service


class CustomerRecommendedActionState(StrEnum):
    ACTIONABLE = "ACTIONABLE"
    RESEARCH_ONLY = "RESEARCH_ONLY"


class CustomerRecommendedPermission(StrEnum):
    MANUAL_REVIEW = "MANUAL_REVIEW"
    NEEDS_FUNDING = "NEEDS_FUNDING"
    SEPARATE_APPROVAL_REQUIRED = "SEPARATE_APPROVAL_REQUIRED"
    RESEARCH_ONLY = "RESEARCH_ONLY"


class CustomerRecommendedActionView(BaseModel):
    state: CustomerRecommendedActionState
    play_id: UUID | None = None
    platform: str | None = None
    action_type: str | None = None
    tactic_id: str | None = None
    opportunity_title: str | None = None
    target_url: str | None = None
    recommended_action: str
    signal_to_watch: str
    portfolio_score: float | None = Field(default=None, ge=0, le=100)
    required_acquisition_usd: float = Field(default=0, ge=0)
    remaining_acquisition_capacity_usd: float = Field(default=0, ge=0)
    topup_amount_usd: float = Field(default=0, ge=0)
    funding_required: bool = False
    permission_state: CustomerRecommendedPermission
    permission_text: str
    execution_allowed: bool = False


@dataclass(frozen=True, slots=True)
class CustomerRecommendedActionResolution:
    view: CustomerRecommendedActionView
    play: DistributionPlayView | None


class CustomerRecommendedActionService:
    """Resolve one current customer action without granting execution permission."""

    def __init__(
        self,
        *,
        funnel_service=customer_funnel_service,
        channel_service=customer_channel_service,
        growth_manager=distribution_growth_manager_service,
        audience_service=audience_intelligence_service,
        analytics_service=distribution_analytics_service,
        balance_service=growth_balance_service,
        funding_service=growth_balance_jit_funding_service,
    ) -> None:
        self._funnel = funnel_service
        self._channels = channel_service
        self._growth_manager = growth_manager
        self._audience = audience_service
        self._analytics = analytics_service
        self._balance = balance_service
        self._funding = funding_service

    def resolve(
        self,
        project_id: UUID,
        customer_token: str,
    ) -> CustomerRecommendedActionResolution:
        project = self._funnel.get_project(project_id, customer_token)
        if project.product_id is None:
            return self._research_only(
                "Finish free research before Partizan recommends an acquisition action."
            )

        channels = {
            channel.platform: channel
            for channel in self._channels.list(project_id, customer_token)
        }
        portfolio = self._growth_manager.portfolio(project.product_id, max_items=50)
        for item in portfolio.items:
            play = item.play
            channel = channels.get(play.platform)
            if channel is None or channel.mode == "OFF":
                continue
            try:
                opportunity = self._audience.find_opportunity(play.opportunity_id)
            except KeyError:
                continue

            if play.tactic_class == DistributionTacticClass.PAID_PLATFORM:
                if (
                    play.action_type != DistributionActionType.PAID_CAMPAIGN
                    or not channel.execution_ready
                ):
                    continue
                required = round(
                    min(float(project.budget_usd), float(play.estimated_cost_max)),
                    2,
                )
                if required <= 0:
                    continue
                analytics = self._analytics.product_analytics(project.product_id)
                balance = self._balance.summary(project_id, analytics.total_spend)
                plan = self._funding.plan(
                    required_acquisition_usd=required,
                    project_budget_usd=float(project.budget_usd),
                    funded_usd=float(balance.funded_usd),
                    acquisition_spend_usd=float(balance.acquisition_spend_usd),
                    remaining_acquisition_capacity_usd=float(
                        balance.remaining_acquisition_capacity_usd
                    ),
                    management_fee_pct=int(balance.management_fee_pct),
                )
                permission_state = (
                    CustomerRecommendedPermission.NEEDS_FUNDING
                    if plan.funding_required
                    else CustomerRecommendedPermission.SEPARATE_APPROVAL_REQUIRED
                )
                permission_text = (
                    "Review and fund only the exact shortfall shown here. Funding does not start "
                    "spend; paid execution still requires a separate approval."
                    if plan.funding_required
                    else "Existing acquisition capacity covers this test. Paid execution still "
                    "requires a separate approval; this recommendation does not start spend."
                )
                return CustomerRecommendedActionResolution(
                    view=CustomerRecommendedActionView(
                        state=CustomerRecommendedActionState.ACTIONABLE,
                        play_id=play.id,
                        platform=play.platform.value,
                        action_type=play.action_type.value,
                        tactic_id=play.tactic_id,
                        opportunity_title=play.opportunity_title,
                        target_url=str(opportunity.url) if opportunity.url is not None else None,
                        recommended_action=self._action_text(play),
                        signal_to_watch=play.success_metric,
                        portfolio_score=item.portfolio_score,
                        required_acquisition_usd=plan.required_acquisition_usd,
                        remaining_acquisition_capacity_usd=(
                            plan.remaining_acquisition_capacity_usd
                        ),
                        topup_amount_usd=plan.topup_amount_usd,
                        funding_required=plan.funding_required,
                        permission_state=permission_state,
                        permission_text=permission_text,
                        execution_allowed=False,
                    ),
                    play=play,
                )

            if opportunity.url is None:
                continue
            return CustomerRecommendedActionResolution(
                view=CustomerRecommendedActionView(
                    state=CustomerRecommendedActionState.ACTIONABLE,
                    play_id=play.id,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    tactic_id=play.tactic_id,
                    opportunity_title=play.opportunity_title,
                    target_url=str(opportunity.url),
                    recommended_action=self._action_text(play),
                    signal_to_watch=play.success_metric,
                    portfolio_score=item.portfolio_score,
                    required_acquisition_usd=0,
                    remaining_acquisition_capacity_usd=0,
                    topup_amount_usd=0,
                    funding_required=False,
                    permission_state=CustomerRecommendedPermission.MANUAL_REVIEW,
                    permission_text=(
                        "Open the researched target and review the move manually. This recommendation "
                        "does not authorize Partizan to publish, reply, comment, or send anything."
                    ),
                    execution_allowed=False,
                ),
                play=play,
            )

        return self._research_only(
            "No ranked action is executable or concrete enough yet. Keep free research going; "
            "Partizan will not ask for acquisition funding until a current action is ready."
        )

    @staticmethod
    def _research_only(message: str) -> CustomerRecommendedActionResolution:
        return CustomerRecommendedActionResolution(
            view=CustomerRecommendedActionView(
                state=CustomerRecommendedActionState.RESEARCH_ONLY,
                recommended_action=message,
                signal_to_watch="A concrete evidence-backed acquisition target.",
                permission_state=CustomerRecommendedPermission.RESEARCH_ONLY,
                permission_text=(
                    "Research only. No channel execution or acquisition spend is authorized."
                ),
                execution_allowed=False,
            ),
            play=None,
        )

    @staticmethod
    def _action_text(play: DistributionPlayView) -> str:
        target = play.opportunity_title
        if play.action_type == DistributionActionType.COMMENT:
            return f"Open {target} and prepare one relevant comment for manual review."
        if play.action_type == DistributionActionType.REPLY:
            return f"Open {target} and prepare one relevant reply for manual review."
        if play.action_type == DistributionActionType.STANDALONE_POST:
            return f"Open {target} and prepare one value-first post for manual review."
        if play.action_type == DistributionActionType.ORGANIC_VIDEO:
            return f"Review {target} and prepare one owned organic video experiment."
        if play.action_type == DistributionActionType.PAID_CAMPAIGN:
            return f"Review one bounded {play.platform.value} paid test for {target}."
        return f"Review the next {play.action_type.value} move for {target}."


customer_recommended_action_service = CustomerRecommendedActionService()
