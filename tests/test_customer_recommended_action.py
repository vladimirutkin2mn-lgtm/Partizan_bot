from types import SimpleNamespace
from uuid import uuid4

from app.customer_recommended_action import (
    CustomerRecommendedActionService,
    CustomerRecommendedActionState,
    CustomerRecommendedPermission,
)
from app.distribution_play_schemas import (
    DistributionPlayStatus,
    DistributionPlayView,
    DistributionTacticClass,
)
from app.distribution_schemas import DistributionOpportunityView
from app.distribution_types import (
    AttributionLevel,
    AutomationLevel,
    DistributionActionType,
    DistributionPlatform,
    OpportunityKind,
)


class _Funnel:
    def __init__(self, product_id, budget_usd=50.0):
        self.project = SimpleNamespace(product_id=product_id, budget_usd=budget_usd)

    def get_project(self, project_id, customer_token):
        return self.project


class _Channels:
    def __init__(self, rows):
        self.rows = rows

    def list(self, project_id, customer_token):
        return self.rows


class _GrowthManager:
    def __init__(self, items):
        self.items = items

    def portfolio(self, product_id, max_items):
        assert max_items >= len(self.items)
        return SimpleNamespace(items=self.items)


class _Audience:
    def __init__(self, opportunities):
        self.opportunities = {item.id: item for item in opportunities}

    def find_opportunity(self, opportunity_id):
        return self.opportunities[opportunity_id]


class _Analytics:
    def product_analytics(self, product_id):
        return SimpleNamespace(total_spend=0.0)


class _Balance:
    def summary(self, project_id, acquisition_spend_usd):
        return SimpleNamespace(
            funded_usd=10.0,
            acquisition_spend_usd=0.0,
            remaining_acquisition_capacity_usd=10.0,
            management_fee_pct=10,
        )


class _Funding:
    def __init__(self):
        self.calls = []

    def plan(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            required_acquisition_usd=kwargs["required_acquisition_usd"],
            remaining_acquisition_capacity_usd=kwargs[
                "remaining_acquisition_capacity_usd"
            ],
            topup_amount_usd=44.45,
            management_fee_pct=kwargs["management_fee_pct"],
            funding_required=True,
        )


def _opportunity(product_id, *, platform, kind, title, url):
    return DistributionOpportunityView(
        id=uuid4(),
        icp_id=uuid4(),
        platform=platform,
        kind=kind,
        canonical_key=f"test:{uuid4()}",
        title=title,
        url=url,
        relevance_score=90,
        rationale="Evidence-backed test opportunity",
    )


def _play(product_id, opportunity, *, paid):
    return DistributionPlayView(
        id=uuid4(),
        product_id=product_id,
        icp_id=opportunity.icp_id,
        opportunity_id=opportunity.id,
        platform=opportunity.platform,
        opportunity_kind=opportunity.kind,
        opportunity_title=opportunity.title,
        tactic_id="instagram_ads" if paid else "instagram_creator_comment",
        tactic_class=(
            DistributionTacticClass.PAID_PLATFORM
            if paid
            else DistributionTacticClass.COMMUNITY
        ),
        action_type=(
            DistributionActionType.PAID_CAMPAIGN
            if paid
            else DistributionActionType.COMMENT
        ),
        automation_level=(
            AutomationLevel.APPROVAL_GATED if paid else AutomationLevel.ASSISTED
        ),
        attribution_level=AttributionLevel.PAID if paid else AttributionLevel.PROFILE,
        identity_required=not paid,
        selected_identity_id=None if paid else uuid4(),
        status=DistributionPlayStatus.READY,
        hypothesis="This concrete acquisition move can produce a measurable first signal.",
        execution_steps=["Review the target.", "Prepare the bounded move."],
        success_metric="first qualified signup",
        estimated_cost_min=100.0 if paid else 0.0,
        estimated_cost_max=500.0 if paid else 20.0,
        effort_hours=1.0,
        time_to_signal_days=3,
        priority_score=90.0 if paid else 80.0,
    )


def _service(product_id, items, opportunities, channels, funding=None):
    return CustomerRecommendedActionService(
        funnel_service=_Funnel(product_id),
        channel_service=_Channels(channels),
        growth_manager=_GrowthManager(items),
        audience_service=_Audience(opportunities),
        analytics_service=_Analytics(),
        balance_service=_Balance(),
        funding_service=funding or _Funding(),
    )


def test_skips_paid_play_when_settlement_channel_is_not_execution_ready() -> None:
    product_id = uuid4()
    paid_opportunity = _opportunity(
        product_id,
        platform=DistributionPlatform.INSTAGRAM,
        kind=OpportunityKind.CREATOR_ACCOUNT,
        title="Paid creator audience",
        url="https://example.com/paid",
    )
    manual_opportunity = _opportunity(
        product_id,
        platform=DistributionPlatform.INSTAGRAM,
        kind=OpportunityKind.CREATOR_ACCOUNT,
        title="Creator discussion",
        url="https://example.com/manual",
    )
    paid = _play(product_id, paid_opportunity, paid=True)
    manual = _play(product_id, manual_opportunity, paid=False)
    service = _service(
        product_id,
        [
            SimpleNamespace(play=paid, portfolio_score=95.0),
            SimpleNamespace(play=manual, portfolio_score=82.0),
        ],
        [paid_opportunity, manual_opportunity],
        [
            SimpleNamespace(
                platform=DistributionPlatform.INSTAGRAM,
                mode="RESEARCH_ONLY",
                execution_ready=False,
            )
        ],
    )

    resolution = service.resolve(uuid4(), "customer-token")

    assert resolution.play is not None
    assert resolution.play.id == manual.id
    assert resolution.view.state == CustomerRecommendedActionState.ACTIONABLE
    assert resolution.view.permission_state == CustomerRecommendedPermission.MANUAL_REVIEW
    assert resolution.view.required_acquisition_usd == 0
    assert resolution.view.funding_required is False
    assert resolution.view.execution_allowed is False
    assert resolution.view.target_url == "https://example.com/manual"


def test_execution_ready_paid_play_uses_server_funding_plan_and_never_grants_execution() -> None:
    product_id = uuid4()
    opportunity = _opportunity(
        product_id,
        platform=DistributionPlatform.INSTAGRAM,
        kind=OpportunityKind.CREATOR_ACCOUNT,
        title="Paid creator audience",
        url="https://example.com/paid",
    )
    paid = _play(product_id, opportunity, paid=True)
    funding = _Funding()
    service = _service(
        product_id,
        [SimpleNamespace(play=paid, portfolio_score=94.0)],
        [opportunity],
        [
            SimpleNamespace(
                platform=DistributionPlatform.INSTAGRAM,
                mode="AUTO",
                execution_ready=True,
            )
        ],
        funding=funding,
    )

    resolution = service.resolve(uuid4(), "customer-token")

    assert resolution.play is not None
    assert resolution.play.id == paid.id
    assert resolution.view.action_type == "PAID_CAMPAIGN"
    assert resolution.view.required_acquisition_usd == 50.0
    assert resolution.view.remaining_acquisition_capacity_usd == 10.0
    assert resolution.view.topup_amount_usd == 44.45
    assert resolution.view.funding_required is True
    assert resolution.view.permission_state == CustomerRecommendedPermission.NEEDS_FUNDING
    assert resolution.view.execution_allowed is False
    assert funding.calls[0]["required_acquisition_usd"] == 50.0


def test_missing_concrete_target_returns_research_only_instead_of_inventing_action() -> None:
    product_id = uuid4()
    opportunity = _opportunity(
        product_id,
        platform=DistributionPlatform.INSTAGRAM,
        kind=OpportunityKind.CREATOR_ACCOUNT,
        title="Unresolved creator surface",
        url=None,
    )
    manual = _play(product_id, opportunity, paid=False)
    service = _service(
        product_id,
        [SimpleNamespace(play=manual, portfolio_score=80.0)],
        [opportunity],
        [
            SimpleNamespace(
                platform=DistributionPlatform.INSTAGRAM,
                mode="RESEARCH_ONLY",
                execution_ready=False,
            )
        ],
    )

    resolution = service.resolve(uuid4(), "customer-token")

    assert resolution.play is None
    assert resolution.view.state == CustomerRecommendedActionState.RESEARCH_ONLY
    assert resolution.view.permission_state == CustomerRecommendedPermission.RESEARCH_ONLY
    assert resolution.view.execution_allowed is False
    assert resolution.view.funding_required is False
