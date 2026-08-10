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
    label: str
    automation_level: AutomationLevel = AutomationLevel.ASSISTED
    attribution_level: AttributionLevel = AttributionLevel.CAMPAIGN
    identity_required: bool = True
    community_policy_required: bool = False
    estimated_cost_min: float = 0
    estimated_cost_max: float = 20
    effort_hours: float = 1.5
    time_to_signal_days: int = 4
    quality_score: float = 7.0
    has_direct_product_link: bool = False
    has_product_mention: bool = False


def _paid(
    tactic_id: str,
    platform: DistributionPlatform,
    kinds: frozenset[OpportunityKind],
    label: str,
    *,
    quality: float = 8.0,
) -> DistributionTacticTemplate:
    return DistributionTacticTemplate(
        tactic_id=tactic_id,
        platform=platform,
        supported_kinds=kinds,
        tactic_class=DistributionTacticClass.PAID_PLATFORM,
        action_type=DistributionActionType.PAID_CAMPAIGN,
        label=label,
        automation_level=AutomationLevel.APPROVAL_GATED,
        attribution_level=AttributionLevel.PAID,
        identity_required=False,
        estimated_cost_min=100,
        estimated_cost_max=500,
        effort_hours=2.5,
        time_to_signal_days=5,
        quality_score=quality,
    )


TACTIC_CATALOG = (
    DistributionTacticTemplate(
        "telegram_channel_comment",
        DistributionPlatform.TELEGRAM,
        frozenset({OpportunityKind.CHANNEL}),
        DistributionTacticClass.COMMUNITY,
        DistributionActionType.COMMENT,
        "relevant comment under a fresh channel post",
        time_to_signal_days=3,
        quality_score=7.5,
    ),
    DistributionTacticTemplate(
        "telegram_group_post",
        DistributionPlatform.TELEGRAM,
        frozenset({OpportunityKind.GROUP}),
        DistributionTacticClass.COMMUNITY,
        DistributionActionType.STANDALONE_POST,
        "relevant standalone group contribution",
        time_to_signal_days=3,
        quality_score=7.5,
    ),
    DistributionTacticTemplate(
        "telegram_group_reply",
        DistributionPlatform.TELEGRAM,
        frozenset({OpportunityKind.GROUP}),
        DistributionTacticClass.COMMUNITY,
        DistributionActionType.REPLY,
        "relevant reply inside an active group conversation",
        effort_hours=1.0,
        time_to_signal_days=3,
    ),
    _paid(
        "telegram_ads",
        DistributionPlatform.TELEGRAM,
        frozenset({OpportunityKind.CHANNEL, OpportunityKind.GROUP}),
        "Telegram Ads test against the discovered audience cluster",
    ),
    DistributionTacticTemplate(
        "instagram_creator_comment",
        DistributionPlatform.INSTAGRAM,
        frozenset({OpportunityKind.CREATOR_ACCOUNT}),
        DistributionTacticClass.COMMUNITY,
        DistributionActionType.COMMENT,
        "relevant comment under a fresh creator Reel/Post",
        attribution_level=AttributionLevel.PROFILE,
    ),
    _paid(
        "instagram_ads",
        DistributionPlatform.INSTAGRAM,
        frozenset({OpportunityKind.CREATOR_ACCOUNT}),
        "Instagram/Meta paid acquisition test informed by creator audience evidence",
    ),
    DistributionTacticTemplate(
        "reddit_value_post",
        DistributionPlatform.REDDIT,
        frozenset({OpportunityKind.SUBREDDIT}),
        DistributionTacticClass.COMMUNITY,
        DistributionActionType.STANDALONE_POST,
        "value-first standalone post where commercial links are permitted",
        attribution_level=AttributionLevel.ACTION,
        community_policy_required=True,
        effort_hours=2.0,
        quality_score=7.5,
        has_direct_product_link=True,
        has_product_mention=True,
    ),
    DistributionTacticTemplate(
        "reddit_comment",
        DistributionPlatform.REDDIT,
        frozenset({OpportunityKind.SUBREDDIT}),
        DistributionTacticClass.COMMUNITY,
        DistributionActionType.COMMENT,
        "relevant comment under a fresh subreddit thread",
        attribution_level=AttributionLevel.PROFILE,
        community_policy_required=True,
        effort_hours=1.0,
    ),
    DistributionTacticTemplate(
        "reddit_reply",
        DistributionPlatform.REDDIT,
        frozenset({OpportunityKind.SUBREDDIT}),
        DistributionTacticClass.COMMUNITY,
        DistributionActionType.REPLY,
        "relevant reply inside a fresh subreddit discussion",
        attribution_level=AttributionLevel.PROFILE,
        community_policy_required=True,
        effort_hours=1.0,
        quality_score=6.5,
    ),
    _paid(
        "reddit_ads",
        DistributionPlatform.REDDIT,
        frozenset({OpportunityKind.SUBREDDIT}),
        "Reddit Ads test against the discovered subreddit audience",
    ),
    DistributionTacticTemplate(
        "tiktok_comment",
        DistributionPlatform.TIKTOK,
        frozenset({OpportunityKind.CONTENT_CLUSTER}),
        DistributionTacticClass.COMMUNITY,
        DistributionActionType.COMMENT,
        "relevant comment under a fresh video inside the topic cluster",
        attribution_level=AttributionLevel.PROFILE,
        quality_score=6.5,
    ),
    DistributionTacticTemplate(
        "tiktok_partizan_organic_video",
        DistributionPlatform.TIKTOK,
        frozenset({OpportunityKind.CONTENT_CLUSTER}),
        DistributionTacticClass.OWNED_ORGANIC,
        DistributionActionType.ORGANIC_VIDEO,
        "Partizan-owned organic video experiment for the discovered topic cluster",
        attribution_level=AttributionLevel.PROFILE,
        estimated_cost_max=50,
        effort_hours=3.0,
        quality_score=8.5,
    ),
    _paid(
        "tiktok_ads",
        DistributionPlatform.TIKTOK,
        frozenset({OpportunityKind.CONTENT_CLUSTER}),
        "TikTok paid acquisition test against the discovered topic cluster",
        quality=8.5,
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
            for template in self._templates_for(opportunity):
                if template.tactic_class == DistributionTacticClass.PAID_PLATFORM:
                    paid_key = (opportunity.icp_id, opportunity.platform, template.tactic_id)
                    if paid_key in paid_keys:
                        continue
                    paid_keys.add(paid_key)
                plays.append(
                    self._build_play(
                        product=product,
                        opportunity=opportunity,
                        template=template,
                        identities=identities,
                        policy=policies.get(opportunity.id),
                        campaign_slots=campaign_slots,
                    )
                )

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
            priority_score=self._priority(opportunity, template),
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
        steps.append(
            "Launch through the configured approval/execution path and measure outcomes."
        )
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
