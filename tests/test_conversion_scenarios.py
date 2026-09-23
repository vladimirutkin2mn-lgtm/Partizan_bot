from types import SimpleNamespace
from uuid import uuid4

from app.conversion_scenarios import (
    ConversionMechanism,
    conversion_scenario_planner,
)
from app.distribution_play_planner import DistributionTacticTemplate
from app.distribution_play_schemas import DistributionTacticClass
from app.distribution_schemas import CommunityPolicyView
from app.distribution_types import (
    DistributionActionType,
    DistributionPlatform,
    OpportunityKind,
)


def _template(*, direct_link: bool = False, product_mention: bool = False):
    return DistributionTacticTemplate(
        tactic_id="test_comment",
        platform=DistributionPlatform.TELEGRAM,
        supported_kinds=frozenset({OpportunityKind.CHANNEL}),
        tactic_class=DistributionTacticClass.COMMUNITY,
        action_type=DistributionActionType.COMMENT,
        label="test comment",
        has_direct_product_link=direct_link,
        has_product_mention=product_mention,
    )


def _play():
    return SimpleNamespace(action_type=DistributionActionType.COMMENT)


def _opportunity():
    return SimpleNamespace()


def test_community_comment_without_promotion_permission_uses_indirect_conversion_paths() -> None:
    variants = conversion_scenario_planner.variants(
        play=_play(),
        opportunity=_opportunity(),
        policy=None,
        template=_template(),
    )

    assert [item.conversion_mechanism for item in variants] == [
        ConversionMechanism.PROFILE_CLICK,
        ConversionMechanism.PROFILE_CLICK,
        ConversionMechanism.REPLY_ENGAGEMENT,
        ConversionMechanism.REPLY_ENGAGEMENT,
    ]
    assert [item.variant_name for item in variants] == [
        "expertise_signal",
        "non_obvious_lens",
        "thoughtful_question",
        "tradeoff_prompt",
    ]


def test_explicit_promotion_permission_adds_direct_and_brand_scenarios() -> None:
    policy = CommunityPolicyView(
        id=uuid4(),
        opportunity_id=uuid4(),
        commercial_participation_allowed=True,
        self_promotion_allowed=True,
        links_allowed=True,
        product_mentions_allowed=True,
        comments_allowed=True,
    )
    variants = conversion_scenario_planner.variants(
        play=_play(),
        opportunity=_opportunity(),
        policy=policy,
        template=_template(direct_link=True, product_mention=True),
    )

    mechanisms = [item.conversion_mechanism for item in variants]
    assert mechanisms[:4] == [
        ConversionMechanism.DIRECT_LINK,
        ConversionMechanism.DIRECT_LINK,
        ConversionMechanism.BRAND_SEARCH,
        ConversionMechanism.BRAND_SEARCH,
    ]
    assert mechanisms.count(ConversionMechanism.PROFILE_CLICK) == 2
    assert mechanisms.count(ConversionMechanism.REPLY_ENGAGEMENT) == 2


def test_policy_blocks_direct_promotion_even_when_tactic_supports_it() -> None:
    policy = CommunityPolicyView(
        id=uuid4(),
        opportunity_id=uuid4(),
        commercial_participation_allowed=True,
        self_promotion_allowed=False,
        links_allowed=True,
        product_mentions_allowed=True,
        comments_allowed=True,
    )
    variants = conversion_scenario_planner.variants(
        play=_play(),
        opportunity=_opportunity(),
        policy=policy,
        template=_template(direct_link=True, product_mention=True),
    )

    mechanisms = {item.conversion_mechanism for item in variants}
    assert ConversionMechanism.DIRECT_LINK not in mechanisms
    assert ConversionMechanism.BRAND_SEARCH not in mechanisms
    assert mechanisms == {
        ConversionMechanism.PROFILE_CLICK,
        ConversionMechanism.REPLY_ENGAGEMENT,
    }
