from dataclasses import dataclass
from uuid import uuid4

from app.distribution_play_schemas import (
    DistributionPlayStatus,
    DistributionPlayView,
    DistributionTacticClass,
)
from app.distribution_policy import (
    DistributionExecutionPolicy,
    DistributionIdentitySelector,
)
from app.distribution_schemas import (
    AudienceDistributionMapView,
    CampaignSlotView,
    CommunityPolicyView,
    DistributionIdentityView,
    DistributionOpportunityView,
)
from app.distribution_types import (
    AttributionLevel,
    AutomationLevel,
    DistributionActionType,
    DistributionPlatform,
    OpportunityKind,
)
from app.schemas import ProductProfileView


@dataclass(frozen=True, slots=True)
class DistributionTacticTemplate:
    tactic_id: str
    platform: DistributionPlatform
    supported_kinds: frozenset[OpportunityKind]
    tactic_class: DistributionTacticClass
    action_type: DistributionActionType
    automation_level: AutomationLevel
    attribution_level: AttributionLevel
    identity_required: bool
    community_policy_required: bool
    label: str
    estimated_cost_min: float
    estimated_cost_max: float
    effort_hours: float
    time_to_signal_days: int
    quality_score: float
    has_direct_product_link: bool = False
    has_product_mention: bool = False


TACTIC_CATALOG = (
    DistributionTacticTemplate(
        tactic_id="telegram_channel_comment",
        platform=DistributionPlatform.TELEGRAM,
        supported_kinds=frozenset({OpportunityKind.CHANNEL}),
        tactic_class=DistributionTacticClass.COMMUNITY,
        action_type=DistributionActionType.COMMENT,
        automation_level=AutomationLevel.ASSISTED,
        attribution_level=AttributionLevel.CAMPAIGN,
        identity_required=True,
        community_policy_required=False,
        label="relevant comment under a fresh channel post",
        estimated_cost_min=0,
        estimated_cost_max=20,
        effort_hours=1.5,
        time_to_signal_days=3,
        quality_score=7.5,
    ),
    DistributionTacticTemplate(
        tactic_id="telegram_group_post",
        platform=DistributionPlatform.TELEGRAM,
        supported_kinds=frozenset({OpportunityKind.GROUP}),
        tactic_class=DistributionTacticClass.COMMUNITY,
        action_type=DistributionActionType.STANDALONE_POST,
        automation_level=AutomationLevel.ASSISTED,
        attribution_level=AttributionLevel.CAMPAIGN,
        identity_required=True,
        community_policy_required=False,
        label="relevant standalone group contribution",
        estimated_cost_min=0,
        estimated_cost_max=20,
        effort_hours=1.5,
        time_to_signal_days=3,
        quality_score=7.5,
    ),
    DistributionTacticTemplate(
        tactic_id="telegram_group_reply",
        platform=DistributionPlatform.TELEGRAM,
        supported_kinds=frozenset({OpportunityKind.GROUP}),
        tactic_class=DistributionTacticClass.COMMUNITY,
        action_type=DistributionActionType.REPLY,
        automation_level=AutomationLevel.ASSISTED,
        attribution_level=AttributionLevel.CAMPAIGN,
        identity_required=True,
        community_policy_required=False,
        label="relevant reply inside an active group conversation",
        estimated_cost_min=0,
        estimated_cost_max=20,
        effort_hours=1.0,
        time_to_signal_days=3,
        quality_score=7.0,
    ),
    DistributionTacticTemplate(
        tactic_id="telegram_ads",
        platform=DistributionPlatform.TELEGRAM,
        supported_kinds=frozenset({OpportunityKind.CHANNEL, OpportunityKind.GROUP}),
        tactic_class=DistributionTacticClass.PAID_PLATFORM,
        action_type=DistributionActionType.PAID_CAMPAIGN,
        automation_level=AutomationLevel.APPROVAL_GATED,
        attribution_level=AttributionLevel.PAID,
        identity_required=False,
        community_policy_required=False,
        label="Telegram Ads test against the discovered audience cluster",
        estimated_cost_min=100,
        estimated_cost_max=500,
        effort_hours=2.0,
        time_to_signal_days=5,
        quality_score=8.0,
    ),
    DistributionTacticTemplate(
        tactic_id="instagram_creator_comment",
        platform=DistributionPlatform.INSTAGRAM,
        supported_kinds=frozenset({OpportunityKind.CREATOR_ACCOUNT}),
        tactic_class=DistributionTacticClass.COMMUNITY,
        action_type=DistributionActionType.COMMENT,
        automation_level=AutomationLevel.ASSISTED,
        attribution_level=AttributionLevel.PROFILE,
        identity_required=True,
        community_policy_required=False,
        label="relevant comment under a fresh creator Reel/Post",
        estimated_cost_min=0,
        estimated_cost_max=20,
        effort_hours=1.5,
        time_to_signal_days=4,
        quality_score=7.0,
    ),
    DistributionTacticTemplate(
        tactic_id="instagram_ads",
        platform=DistributionPlatform.INSTAGRAM,
        supported_kinds=frozenset({OpportunityKind.CREATOR_ACCOUNT}),
        tactic_class=DistributionTacticClass.PAID_PLATFORM,
        action_type=DistributionActionType.PAID_CAMPAIGN,
        automation_level=AutomationLevel.APPROVAL_GATED,
        attribution_level=AttributionLevel.PAID,
        identity_required=False,
        community_policy_required=False,
        label="Instagram/Meta paid acquisition test informed by creator audience evidence",
        estimated_cost_min=100,
        estimated_cost_max=500,
        effort_hours=3.0,
        time_to_signal_days=5,
        quality_score=8.0,
    ),
    DistributionTacticTemplate(
        tactic_id="reddit_value_post",
        platform=DistributionPlatform.REDDIT,
        supported_kinds=frozenset({OpportunityKind.SUBREDDIT}),
        tactic_class=DistributionTacticClass.COMMUNITY,
        action_type=DistributionActionType.STANDALONE_POST,
        automation_level=AutomationLevel.ASSISTED,
        attribution_level=AttributionLevel.ACTION,
        identity_required=True,
        community_policy_required=True,
        label="value-first standalone post where commercial links are permitted",
        estimated_cost_min=0,
        estimated_cost_max=20,
        effort_hours=2.0,
        time_to_signal_days=4,
        quality_score=7.5,
        has_direct_product_link=True,
        has_product_mention=True,
    ),
    DistributionTacticTemplate(
        tactic_id="reddit_comment",
        platform=DistributionPlatform.REDDIT,
        supported_kinds=frozenset({OpportunityKind.SUBREDDIT}),
        tactic_class=DistributionTacticClass.COMMUNITY,
        action_type=DistributionActionType.COMMENT,
        automation_level=AutomationLevel.ASSISTED,
        attribution_level=AttributionLevel.PROFILE,
        identity_required=True,
        community_policy_required=True,
        label="relevant comment under a fresh subreddit thread",
        estimated_cost_min=0,
        estimated_cost_max=20,
        effort_hours=1.0,
        time_to_signal_days=4,
        quality_score=7.0,
    ),
    DistributionTacticTemplate(
        tactic_id="reddit_reply",
        platform=DistributionPlatform.REDDIT,
        supported_kinds=frozenset({OpportunityKind.SUBREDDIT}),
        tactic_class=DistributionTacticClass.COMMUNITY,
        action_type=DistributionActionType.REPLY,
        automation_level=AutomationLevel.ASSISTED,
        attribution_level=AttributionLevel.PROFILE,
        identity_required=True,
        community_policy_required=True,
        label="relevant reply inside a fresh subreddit discussion",
        estimated_cost_min=0,
        estimated_cost_max=20,
        effort_hours=1.0,
        time_to_signal_days=4,
        quality_score=6.5,
    ),
    DistributionTacticTemplate(
        tactic_id="reddit_ads",
        platform=DistributionPlatform.REDDIT,
        supported_kinds=frozenset({OpportunityKind.SUBREDDIT}),
        tactic_class=DistributionTacticClass.PAID_PLATFORM,
        action_type=DistributionActionType.PAID_CAMPAIGN,
        automation_level=AutomationLevel.APPROVAL_GATED,
        attribution_level=AttributionLevel.PAID,
        identity_required=False,
        community_policy_required=False,
        label="Reddit Ads test against the discovered subreddit audience",
        estimated_cost_min=100,
        estimated_cost_max=500,
        effort_hours=2.5,
        time_to_signal_days=5,
        quality_score=8.0,
    ),
    DistributionTacticTemplate(
        tactic_id="tiktok_comment",
        platform=DistributionPlatform.TIKTOK,
        supported_kinds=frozenset({OpportunityKind.CONTENT_CLUSTER}),
        tactic_class=DistributionTacticClass.COMMUNITY,
        action_type=DistributionActionType.COMMENT,
        automation_level=AutomationLevel.ASSISTED,
        attribution_level=AttributionLevel.PROFILE,
        identity_required=True,
        community_policy_required=False,
        label="relevant comment under a fresh video inside the topic cluster",
        estimated_cost_min=0,
        estimated_cost_max=20,
        effort_hours=1.5,
        time_to_signal_days=4,
        quality_score=6.5,
    ),
    DistributionTacticTemplate(
        tactic_id="tiktok_partizan_organic_video",
        platform=DistributionPlatform.TIKTOK,
        supported_kinds=frozenset({OpportunityKind.CONTENT_CLUSTER}),
        tactic_class=DistributionTacticClass.OWNED_ORGANIC,
        action_type=DistributionActionType.ORGANIC_VIDEO,
        automation_level=AutomationLevel.ASSISTED,
        attribution_level=AttributionLevel.PROFILE,
        identity_required=True,
        community_policy_required=False,
        label="Partizan-owned organic video experiment for the discovered topic cluster",
        estimated_cost_min=0,
        estimated_cost_max=50,
        effort_hours=3.0,
        time_to_signal_days=4,
        quality_score=8.5,
    ),
    DistributionTacticTemplate(
        tactic_id="tiktok_ads",
        platform=DistributionPlatform.TIKTOK,
        supported_kinds=frozenset({OpportunityKind.CONTENT_CLUSTER}),
        tactic_class=DistributionTacticClass.PAID_PLATFORM,
        action_type=DistributionActionType.PAID_CAMPAIGN,
        automation_level=AutomationLevel.APPROVAL_GATED,
        attribution_level=AttributionLevel.PAID,
        identity_required=False,
        community_policy_required=False,
        label="TikTok paid acquisition test against the discovered topic cluster",
        estimated_cost_min=100,
        estimated_cost_max=500,
        effort_hours=3.0,
        time_to_signal_days=5,
        quality_score=8.5,
    ),
)


class DistributionPlayPlanner:
    def __init__(self) -> None:
        self._execution_policy = DistributionExecutionPolicy()
        self._identity_selector = DistributionIdentitySelector()

    def plan(
        self,
        *,
        product: ProductProfileView,
        distribution_map: AudienceDistributionMapView,
        identities: list[DistributionIdentityView] | None = None,
        community_policies: list[CommunityPolicyView] | None = None,
        campaign_slots: list[CampaignSlotView] | None = None,
        max_plays: int = 40,
    ) -> list[DistributionPlayView]:
        identities = identities or []
        campaign_slots = campaign_slots or []
        policies = {policy.opportunity_id: policy for policy in community_policies or []}
        opportunities = sorted(
            distribution_map.opportunities,
            key=lambda item: (-(item.relevance_score or 0), str(item.id)),
        )
        plays: list[DistributionPlayView] = []
        paid_keys: set[tuple[object, DistributionPlatform, str]] = set()

        for opportunity in opportunities:
            templates = self._templates_for(opportunity)
            for template in templates:
                if template.tactic_class == DistributionTacticClass.PAID_PLATFORM:
                    paid_key = (opportunity.icp_id, opportunity.platform, template.tactic_id)
                    if paid_key in paid_keys:
                        continue
                    paid_keys.add(paid_key)

                play = self._build_play(
                    product=product,
                    opportunity=opportunity,
                    template=template,
                    identities=identities,
                    policy=policies.get(opportunity.id),
                    campaign_slots=campaign_slots,
                )
                plays.append(play)

        plays.sort(
            key=lambda play: (
                play.status != DistributionPlayStatus.READY,
                -play.priority_score,
                play.tactic_id,
                str(play.opportunity_id),
            )
        )
        return plays[:max_plays]

    def _templates_for(
        self,
        opportunity: DistributionOpportunityView,
    ) -> list[DistributionTacticTemplate]:
        return [
            template
            for template in TACTIC_CATALOG
            if template.platform == opportunity.platform
            and opportunity.kind in template.supported_kinds
        ]

    def _build_play(
        self,
        *,
        product: ProductProfileView,
        opportunity: DistributionOpportunityView,
        template: DistributionTacticTemplate,
        identities: list[DistributionIdentityView],
        policy: CommunityPolicyView | None,
        campaign_slots: list[CampaignSlotView],
    ) -> DistributionPlayView:
        selection = None
        if template.identity_required:
            selection = self._identity_selector.select(
                product_id=product.id,
                opportunity=opportunity,
                identities=identities,
                campaign_slots=campaign_slots,
                desired_language=product.language,
                action_type=template.action_type,
            )

        decision = self._execution_policy.evaluate(
            opportunity,
            template.action_type,
            identity=selection.identity if selection else None,
            community_policy=policy,
            has_direct_product_link=template.has_direct_product_link,
            has_product_mention=template.has_product_mention,
        )
        blockers = list(dict.fromkeys(decision.reasons))
        status = (
            DistributionPlayStatus.READY if decision.allowed else DistributionPlayStatus.BLOCKED
        )
        cost_min, cost_max = self._cap_cost(
            template.estimated_cost_min,
            template.estimated_cost_max,
            product.budget,
        )
        priority = self._priority(opportunity, template)
        rationale = [
            f"Opportunity relevance={opportunity.relevance_score or 0:.1f}/100.",
            f"Tactic quality hypothesis={template.quality_score:.1f}/10.",
            f"Automation level={template.automation_level.value}.",
            f"Attribution level={template.attribution_level.value}.",
        ]
        if selection is not None:
            rationale.append(
                f"Selected Distribution Identity score={selection.score:.1f}/100."
            )
            rationale.extend(selection.reasons)
        if decision.disclosure_required:
            rationale.append("Community policy requires disclosure in the contribution.")

        goal = product.goal or "activated and paid users at acceptable CAC"
        return DistributionPlayView(
            id=uuid4(),
            product_id=product.id,
            icp_id=opportunity.icp_id,
            opportunity_id=opportunity.id,
            platform=opportunity.platform,
            opportunity_kind=opportunity.kind,
            opportunity_title=opportunity.title,
            tactic_id=template.tactic_id,
            tactic_class=template.tactic_class,
            action_type=template.action_type,
            automation_level=template.automation_level,
            attribution_level=template.attribution_level,
            identity_required=template.identity_required,
            selected_identity_id=selection.identity.id if selection else None,
            community_policy_required=template.community_policy_required,
            status=status,
            blockers=blockers,
            hypothesis=(
                f"If Partizan uses {template.label} around {opportunity.title}, "
                f"the experiment can produce measurable progress toward {goal}."
            ),
            execution_steps=self._steps(template, opportunity),
            success_metric=goal,
            estimated_cost_min=cost_min,
            estimated_cost_max=cost_max,
            effort_hours=template.effort_hours,
            time_to_signal_days=template.time_to_signal_days,
            priority_score=priority,
            rationale=rationale,
        )

    def _steps(
        self,
        template: DistributionTacticTemplate,
        opportunity: DistributionOpportunityView,
    ) -> list[str]:
        steps = [
            f"Use {opportunity.title} as the concrete distribution opportunity.",
            f"Prepare {template.label} with campaign-specific attribution metadata.",
        ]
        if template.community_policy_required:
            steps.insert(1, "Verify CommunityPolicy before preparing the external action.")
        if template.identity_required:
            steps.append("Use the selected active Partizan Distribution Identity.")
        steps.append("Launch only through the configured approval/execution path and measure outcomes.")
        return steps

    def _priority(
        self,
        opportunity: DistributionOpportunityView,
        template: DistributionTacticTemplate,
    ) -> float:
        relevance = opportunity.relevance_score or 0
        return round(min(100.0, relevance * 0.7 + template.quality_score * 3.0), 1)

    def _cap_cost(
        self,
        cost_min: float,
        cost_max: float,
        budget: float | None,
    ) -> tuple[float, float]:
        if budget is None or budget <= 0:
            return cost_min, cost_max
        capped_max = min(cost_max, budget)
        capped_min = min(cost_min, capped_max)
        return capped_min, capped_max
